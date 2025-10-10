''' Web views which provide the ability to test DICOM resources for metadata which match known
	tags such as DICOM distortion.
'''
import json, uuid, logging, traceback

import client.apisettings as gcapicodes
from client.utils.object import pick, omit

from pydantic import ValidationError as PydanticValidationError

from client.errors import ConfigurationError, ResourceDoesNotExist
from client.utils.general import first

from sonador import apisettings as sonador_api
from sonador.serialization import SonadorJsonEncoder

from .. import apisettings as sonador_orthanc_api
from ..db.distortionfilter import DistortionFilterDevice
from ..db.helpers import orthanc_devicejson
from ..db.cache import CacheSeries, CacheStudy

from ..validation.base import OrthancViewValidationMixin
from ..validation.distortionfilter import DistortionFilterDeviceValidationForm

from ..cache.web import CacheBaseView, ResourceUidMixin

from .base import OrthancBaseView
from .helpers import paginate_query_results
from .ext import ObjectManagementView, ObjectRestView
from .ext.group import GroupChildManagementBaseView, GroupChildBaseRestView, GroupChildViewMixin
from .dicomweb import DicomResourceMixin, DicomUidJsonMixin
from ..web.secure_user import UserContextMixin, GroupLookupMixin

logger = logging.getLogger(__name__)


def check_dicom_compatibility(devices, filter_dicom_tags):
	'''	Run distortion filter check on a DICOM Series. This will check every device in the 
		database and see if the distortion DICOM tag is matching the one in the Series. 
	
		The sequence executes as follows:
	
		1. Grab list of devices from Orthanc DB
		2. Iterate through each Device and see if DICOM values exists for 
	   		Manufacturer, Institution, and Model #. If none of those values exist or if they 
	   		do not align with the current Series device values it will ignore this device 
	   		(return payload will state "Ignore" for this device) and continue on to the next device.
		3. If they device is not ignored it will then check the value for DcmTag / DicomTagValue 
	   		and see if those tags exist and match the ones in this current series. 
	   		If they do match it will return in the payload a "Filter Applied" for this device value. 
	   	
	   	Otherwise it will return a "Filter Not Applied" with an "Error" message for the UI to flag and display. 
	'''
	results = []

	for index, device in enumerate(devices):
        
        # Normalize DICOM Tag Name format for comparison
		dicom_tag = device['DcmTag'].replace('(', '').replace(')', '').replace(' ', '').lower()
		
		# Condition 1: Check if device information matches
		if (filter_dicom_tags.get(','.join(sonador_api.DCMCODE_MANUFACTURER), {}) and \
            filter_dicom_tags.get(','.join(sonador_api.DCMCODE_MANUFACTER_MODEL_NAME), {}) and \
            filter_dicom_tags.get(','.join(sonador_api.DCMCODE_SOFTWARE_VERSIONS), {})):
			if (
				device[sonador_api.DCMHEADER_MANUFACTURER] == filter_dicom_tags[','.join(sonador_api.DCMCODE_MANUFACTURER)]['Value'] and
				device[sonador_api.DCMHEADER_MANUFACTER_MODEL_NAME] == filter_dicom_tags[','.join(sonador_api.DCMCODE_MANUFACTER_MODEL_NAME)]['Value'] and
				device[sonador_api.DCMHEADER_SOFTWARE_VERSIONS] == filter_dicom_tags[','.join(sonador_api.DCMCODE_SOFTWARE_VERSIONS)]['Value']
			):
				# Condition 2: Check if DICOM Tag Name and DICOM Tag Value match
				if dicom_tag in filter_dicom_tags and filter_dicom_tags[dicom_tag]['Value'] == device['DcmTagValue']:
					results.append({
						sonador_orthanc_api.DISTORTION_FILTER_INDEX: index,
						sonador_orthanc_api.DISTORTION_FILTER_DEVICE_UID: device['ID'],
						sonador_orthanc_api.DISTORTION_FILTER_DEVICE_MODEL: device[sonador_api.DCMHEADER_MANUFACTER_MODEL_NAME], 
					 	sonador_orthanc_api.DISTORTION_FILTER_RESULT: sonador_orthanc_api.DISTORTION_FILTER_RESULT_APPLIED, 
					 	sonador_orthanc_api.DISTORTION_FILTER_MESSAGE: 'Filter Applied for SeriesNumber="%s" on Device="%s" Name="%s" Version="%s"' % (
					 		filter_dicom_tags[','.join(sonador_api.DCMCODE_SERIES_NUMBER)]['Value'], index, 
					 		device[sonador_api.DCMHEADER_MANUFACTER_MODEL_NAME], device[sonador_api.DCMHEADER_SOFTWARE_VERSIONS],
					 	),
					 })

				else:
					results.append({
						sonador_orthanc_api.DISTORTION_FILTER_INDEX: index, 
						sonador_orthanc_api.DISTORTION_FILTER_DEVICE_UID: device['ID'], 
						sonador_orthanc_api.DISTORTION_FILTER_DEVICE_MODEL: device[sonador_api.DCMHEADER_MANUFACTER_MODEL_NAME],
					 	sonador_orthanc_api.DISTORTION_FILTER_RESULT: sonador_orthanc_api.DISTORTION_FILTER_RESULT_NOT_APPLIED, 
					 	sonador_orthanc_api.DISTORTION_FILTER_ERROR: 'SeriesNumber="%s" geometry distortion filter was not applied for Device="%s" Name="%s" Version="%s"' % (
					 		filter_dicom_tags[','.join(sonador_api.DCMCODE_SERIES_NUMBER)]['Value'], index, 
					 		device[sonador_api.DCMHEADER_MANUFACTER_MODEL_NAME], device[sonador_api.DCMHEADER_SOFTWARE_VERSIONS]
					 	),
					 })
			else:
				# Condition 3: Device information does not match
				results.append({
					sonador_orthanc_api.DISTORTION_FILTER_INDEX: index, 
					sonador_orthanc_api.DISTORTION_FILTER_DEVICE_UID: device['ID'], 
					sonador_orthanc_api.DISTORTION_FILTER_DEVICE_MODEL: device[sonador_api.DCMHEADER_MANUFACTER_MODEL_NAME],
					sonador_orthanc_api.DISTORTION_FILTER_RESULT: sonador_orthanc_api.DISTORTION_FILTER_RESULT_IGNORE, 
				})

		else:
				# Condition 3: Device information does not match
				results.append({
					sonador_orthanc_api.DISTORTION_FILTER_INDEX: index,
					sonador_orthanc_api.DISTORTION_FILTER_DEVICE_UID: device['ID'],
					sonador_orthanc_api.DISTORTION_FILTER_DEVICE_MODEL: device[sonador_api.DCMHEADER_MANUFACTER_MODEL_NAME],
					sonador_orthanc_api.DISTORTION_FILTER_RESULT: sonador_orthanc_api.DISTORTION_FILTER_RESULT_IGNORE, 
				})
    
	return results


class DistortionFilterDeviceManagementView(UserContextMixin, GroupChildManagementBaseView):
	'''	View instance which an be used to create and get device resources from Orthanc.
	'''
	sessionmaker = None
	model = DistortionFilterDevice
	modelform = DistortionFilterDeviceValidationForm

	orthanc_objectjson = lambda _,device: orthanc_devicejson(device)
	
	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request) 

	def modelform_kwargs(self, *args, **kwargs):
		'''	Add keyword arguments for modelform's "clean" method
		'''
		form_kwargs = super().modelform_kwargs(*args, **kwargs)

		# Initialize user context and add user and user groups
		self.init_user_context(self.request, *args, **kwargs)
		form_kwargs.update({
			'request_user': self.user, 'request_user_groups': self.groups,
		})
		
		return form_kwargs

	def get_response_headers(self, response, status_code, method, *args, **kwargs):
		'''	Add operations and permissions headers to collection (GET) requests
		'''
		headers = super().get_response_headers(response, status_code, method, *args, **kwargs)
		if method == gcapicodes.HTTP_GET:

			# Initialize user context if user data is not already available
			if getattr(self, 'user', None) is None:
				self.init_user_context(self.request, *args, **kwargs)

			_user, _group = self.user, self.get_group(*args, **kwargs)
			_group_acl = self.sonador_manager.get_internal_imageserver().fetch_acl().get_group_acl(_group.pk)

			# Add Distortion Filter Group Permissions
			headers[sonador_api.SONADOR_PERMISSIONS_HEADER] = json.dumps({
				'devices_list': _group_acl.devices_list or _user.is_superuser,
				'devices_list_modify': _group_acl.devices_list_modify or _user.is_superuser,
			}, cls=SonadorJsonEncoder)

			# Ensure that the headers are visible in the response
			exposed_headers = headers.get(gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER) \
				or headers.get(gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER.lower()) \
				or headers.get(gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER.upper()) \
				or []

			exposed_headers.append(sonador_api.SONADOR_PERMISSIONS_HEADER)
			headers[gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER] = ', '.join(exposed_headers)
			

		return headers


class DistortionFilterDeviceRestView(UserContextMixin, GroupChildBaseRestView):
	'''	REST endpoint which can be used to get, put, and delete a specified device from Orthanc
	'''
	sessionmaker = None

	model = DistortionFilterDevice
	modelform = DistortionFilterDeviceValidationForm
	
	success_status_code = 201
	error_status_code = 400

	orthanc_objectjson = lambda _,device: orthanc_devicejson(device)

	def modelform_kwargs(self, **kwargs):
		'''	Add keyword arguments for modelform's "clean" method
		'''
		form_kwargs = super().modelform_kwargs(**kwargs)

		# Initialize user context and add user and use groups
		self.init_user_context(self.request, **kwargs)
		form_kwargs.update({
			'request_user': self.user, 'request_user_groups': self.groups,
		})

		return form_kwargs
	

class DistortionFilterView(GroupChildViewMixin, ResourceUidMixin, CacheBaseView):
	'''	Distortion Filter View Check against DICOM and Master List of Devices.
	'''
	sessionmaker = None
	resource_cachemodel = CacheStudy
	model = DistortionFilterDevice

	success_status_code = 201
	error_status_code = 400

	orthanc_objectjson = lambda _,device: orthanc_devicejson(device)

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		# Set GET request and general query parameters
		request = request or {}
		self.GET = self.request.get('get', {})

		# Ensure that a resource model has been defined and an index method is available
		self.init_resource_mixin(*args, **kwargs)
	
	def get(self, session, *args, **kwargs):
		'''	Return a list of devices which match the request parameters
		'''
		try:
			with self.sessionmaker() as session:

				# Retrieve group
				group = self.get_group(*args, **kwargs)
				
				# Retrieve study 
				r = self.get_resource(session, *args, **kwargs)
				iserver = self.sonador_manager.get_internal_imageserver()
				study = iserver.get_study(r.publicid)

				# Retrieve devices and create devices list
				devices = session.query(self.model).filter_by(group=int(group.pk))
				devices_list = [self.orthanc_objectjson(d) for d in devices]

				# Test study against the devices list
				final_results = {}
				for i in study.series:
					series = iserver.get_series(i)
					tags = series.instances_collection[0].dcmtags
					compatibility_results = check_dicom_compatibility(devices_list, tags)
					final_results[series.series_uid] = {'series_id': i, 'results': compatibility_results}

				# Return DICOM resource and device master list
				return self.send_response(json.dumps(
					final_results,
					cls=SonadorJsonEncoder))
		
		except ResourceDoesNotExist as err:			
			response = {
				gcapicodes.ERROR: 'Resource %s=%s does not exist' % (
					self.resource_cachemodel.type, self.get_resource_uid() or '(none)'),
				gcapicodes.STATUS: gcapicodes.FAIL
			}

			return self.http404_resource_not_found(response=response)
		
		except Exception as err:
			logger.error('Unable to filter %s=%s due to error. Error: %s\n%s'
				% (self.model, self.get_resource_uid(*args, **kwargs), err, traceback.format_exc()))

			return self.send_response(json.dumps({
				'error': str(err), gcapicodes.STATUS: gcapicodes.FAIL
			}), status_code=400)


class DeviceDistortionDICOMView(DicomUidJsonMixin, DicomResourceMixin, DistortionFilterView):
	'''	DICOMweb REST view: retrieve individual Auth details, update, and delete auth grants
	'''
	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self._init_dicom_json(*args, **kwargs)

	def get_resource_uid(self, *args, resource_uri=None, **kwargs):
		'''	Retrieve the UID of the DICOM resource from the provided resource URI.
			Distortion filter endpoints also include a group value with DICOM UID values
			at the end, which means that they need to be processed in reverse order.
		'''
		# Seed the DICOM resource URI by locating the first URL component without alphabetic characters,
		# starting at the end of the URL and working towards the front.
		resource_uri = resource_uri or first(reversed(self.uri.split('/')), key=lambda s: s.replace('.', '').isnumeric())
		return super().get_resource_uid(*args, resource_uri=resource_uri, **kwargs)
