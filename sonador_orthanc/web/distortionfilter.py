''' Web views which provide an API compatible with the Unified Procedure Step DICOM specification.
	UPS facilitates the retrieval of scheduled procedures via worklist queries and allows for
	modalities to report when a worklist item as been fulfilled.
'''
import json, uuid, logging, traceback
import client.apisettings as gcapicodes

from pydantic import ValidationError as PydanticValidationError

from client.errors import ConfigurationError, ResourceDoesNotExist
from sonador.serialization import SonadorJsonEncoder

from ..db.distortionfilter import DistortionFilterDevice
from ..db.helpers import orthanc_devicejson
from ..db.cache import CacheSeries, CacheStudy

from ..validation.base import OrthancViewValidationMixin
from ..validation.distortionfilter import DistortionFilterDeviceValidationForm

from ..cache.web import CacheBaseView, ResourceUidMixin

from .base import OrthancBaseView
from .helpers import paginate_query_results
from .ext import ObjectManagementView, ObjectRestView
from .dicomweb import DicomResourceMixin, DicomUidJsonMixin

logger = logging.getLogger(__name__)


def check_dicom_compatibility(devices, filter_dicom_tags):
	'''Run distortion filter check on a DICOM Series. This will check every device in the 
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
		if (filter_dicom_tags.get('0008,0070', {}) and \
            filter_dicom_tags.get('0008,1090', {}) and \
            filter_dicom_tags.get('0018,1020', {})):
			if (
				device['Manufacturer'] == filter_dicom_tags['0008,0070']['Value'] and
				device['ManufacturerModelName'] == filter_dicom_tags['0008,1090']['Value'] and
				device['SoftwareVersions'] == filter_dicom_tags['0018,1020']['Value']
			):
				# Condition 2: Check if DICOM Tag Name and DICOM Tag Value match
				if dicom_tag in filter_dicom_tags and filter_dicom_tags[dicom_tag]['Value'] == device['DcmTagValue']:
					results.append({'device_id': index, 'orthanc_device_id': device['ID'], 'Device Model': device['ManufacturerModelName'], 
					 'result': 'Filter Applied', 'error': '', 'message': 'Filter Applied for Series number %s on Device: %s Name: %s Version %s' % (filter_dicom_tags['0020,0011']['Value'], index, device['ManufacturerModelName'], device['SoftwareVersions'])})
				else:
					results.append({'device_id': index, 'orthanc_device_id': device['ID'], 'Device Model': device['ManufacturerModelName'],
					 'result': 'Filter Not Applied', 
					#  'log': 'Device Tag: %s, Device Tag Value: %s, Series Tag: %s, Seires Dicom Tags: %s' % (dicom_tag, device['DcmTagValue'], dicom_tag, filter_dicom_tags),
					 'error': 'Series number %s geometry distortion filter was not applied for Device: %s Name: %s Version %s' % (filter_dicom_tags['0020,0011']['Value'], index, device['ManufacturerModelName'], device['SoftwareVersions'])})
			else:
				# Condition 3: Device information does not match
				results.append({'device_id': index, 'orthanc_device_id': device['ID'], 'Device Model': device['ManufacturerModelName'],
					'result': 'Ignore', 'error': ''})
		else:
				# Condition 3: Device information does not match
				results.append({'device_id': index, 'orthanc_device_id': device['ID'], 'Device Model': device['ManufacturerModelName'],
					'result': 'Ignore', 'error': ''})
    
	return results


class DistortionFilterDeviceManagementView(ObjectManagementView):
	'''	View instance which an be used to create and get device resources from Orthanc.
	'''
	sessionmaker = None
	model = DistortionFilterDevice
	modelform = DistortionFilterDeviceValidationForm

	orthanc_objectjson = lambda _,device: orthanc_devicejson(device)
	
	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)
  
	def get_objects(self, session, *args, **kwargs):
		''' Retrieve objects from the database using the provided session. '''
		return session.query(self.model).all()

	def init_object_model(self, *args, **kwargs):
		''' Initialize a new instance of the model. '''
		return self.model(uid=str(uuid.uuid4()))


class DistortionFilterDeviceRestView(ObjectRestView):
	'''	REST endpoint which can be used to get, put, and delete a specified device from Orthanc
	'''
	sessionmaker = None

	model = DistortionFilterDevice
	modelform = DistortionFilterDeviceValidationForm
	
	success_status_code = 201
	error_status_code = 400

	orthanc_objectjson = lambda _,device: orthanc_devicejson(device)

	def get_object(self, session, *args, uid=None, **kwargs):
		'''	Retrieve a device for the provided ID. Throws ResourceDoesNotExist
			if unable to find device with the provided
			UID associated with the series.

			@returns device instance
		'''
		# Retrieve device UID
		uid = uid or self.get_object_uid(*args, **kwargs)

		device = session.query(self.model).filter_by(uid=uid).first()
		if not device:
			raise ResourceDoesNotExist('Unable to retrieve device ID=%s' % uid)

		return device
	

class DistortionFilterView(OrthancViewValidationMixin, ResourceUidMixin, CacheBaseView):
	'''	Distortion Filter View Check against DICOM and Master List of Devices.
	'''
	sessionmaker = None
	resource_cachemodel = CacheStudy
	model = DistortionFilterDevice
	modelform = DistortionFilterDeviceValidationForm

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

	def modelform_kwargs(self, **kwargs):
		'''	Add keyword arguments for modelform's "clean" method
		'''
		return {}
	
	def get(self, session, *args, **kwargs):
		'''	Return a list of devices which match the request parameters
		'''
		try:
			with self.sessionmaker() as session:
				rid = self.get_resource_uid(session, *args, **kwargs)
				resource = self.get_resource(session, *args, **kwargs)
				rid = resource.publicid
				devices = session.query(self.model).all()
				devices_list = [self.orthanc_objectjson(d) for d in devices]

				iserver = self.sonador_manager.get_internal_imageserver()
				study = iserver.get_study(rid)		

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
				gcapicodes.ERROR: 'Resource %s=%s does not exist' % (self.resource_cachemodel.type, rid or '(none)'),
				gcapicodes.STATUS: gcapicodes.FAIL
			}

			return self.http404_resource_not_found(response=response)
		
		except Exception as err:
			logger.error('Unable to filter %s=%s due to error. Error: %s\n%s'
				% (self.model, rid, err, traceback.format_exc()))

			return self.send_response(json.dumps({
				'error': str(err), gcapicodes.STATUS: gcapicodes.FAIL
			}), status_code=400)


class DeviceDistortionDICOMView(DicomUidJsonMixin, DicomResourceMixin, DistortionFilterView):
	'''	DICOMweb REST view: retrieve individual Auth details, update, and delete auth grants
	'''
	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self._init_dicom_json(*args, **kwargs)
