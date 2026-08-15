'''	Orthanc views providing DICOMweb compatability
'''
import posixpath, pydicom, logging, json, copy, datetime, traceback, gzip, re
from io import BytesIO
import orthanc

from sqlalchemy.orm import joinedload

import client.apisettings as gcapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist
from client.utils.object import omit, pick
from client.utils.general import first
from client.utils.conversion import str2bool

from sonador.apisettings import \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES,  \
	DICOM_UID_REGEX, DCMHEADER_STUDY_INSTANCE_UID, DCMHEADER_SERIES_INSTANCE_UID, DCMHEADER_MODALITIES_IN_STUDY, \
	DCMHEADER_MODALITY, DCMHEADER_STUDY_DATE, DCM_DICOM_CONTENT_TYPE, DCM_CONTENT_TYPE, DCM_JSON_MIMETYPE
from sonador.imaging.helpers.conversion import json2dcmjson
from sonador.helpers.local import dcm_part10_backfill
from sonador.serialization import dcm_str2date, SonadorJsonEncoder

from sonador_orthanc_common.web import RedirectView, dcmweb_encode_multipart_single

from .. import apisettings as sonador_api
from ..apisettings import ORTHANC_CONFIG_SECTION_DICOMWEB, ORTHANC_CONFIG_SECTION_POSTGRES, \
	ORTHANC_CONFIG_SECTION_EXTRADICOMTAGS, ORTHANC_MAINDICOM_TAGS_DEFAULT, SONADOR_CONF_PRIVATE_TAGS, \
	SONADOR_CACHE_ORDER_BY, REQUIRE_EXPLICIT_ACCESS_QUERY_PARAM
from ..helpers import orthanc_maindicom_tags
from ..db.internal import Resource, DicomIdentifiers
from ..db.cache import CachePatient, CacheStudy, CacheSeries, CacheInstance
from ..db.helpers import dcmquery2psqlregex, dcmuid_fetch_dicomidentifier_model
from ..dcmquery.auth import StudyResourceAclMixin
from ..dcmquery.base import UnorderableDicomHeader

from ..cache.web import ResourceBaseMixin
from ..cache.web.study import CacheStudyListBaseView, SonadorStudyResourceMixin
from ..cache.web.series import SonadorSeriesResourceMixin
from ..cache.web.secure_search import SecureResourceQueryViewMixin

from .base import OrthancBaseView
from .secure_user import UserContextMixin
from .resource import SonadorResourceMixin

logger = logging.getLogger(__name__)


class CacheStudyDicomWebListView(StudyResourceAclMixin, SecureResourceQueryViewMixin, UserContextMixin, CacheStudyListBaseView):
	'''	DICOMweb REST study list endpoint which is able to use the Sonador database cache.
	'''
	sonador_manager = None
	limit_default = 100
	offset_default = 0

	def setup(self, output, uri, request, *args, **kwargs):

		if not self.sonador_manager:
			raise ConfigurationError('Unable to initialize DICOMweb study list view, invalid Sonador manager')

		# Set GET request and general query parameters
		request = request or {}
		self.dicom_query = omit(request.get('get', {}),
			('limit', 'offset', 'fuzzymatching', 'includefield', DCMHEADER_MODALITIES_IN_STUDY, DCMHEADER_STUDY_DATE))

		super().setup(output, uri, request)
		self.init_user_context(request, *args, **kwargs)

		# Ensure that there is an identified user associated with the request
		if not self.user:
			raise ValueError('Unable to process secure query, invalid user instance')

		# Retrieve URL query parameters from request, pull DICOM query parameters from GET
		self.GET = self.request.get('get', {})
		self.force_apply_queryfilter = str2bool(self.GET.get(REQUIRE_EXPLICIT_ACCESS_QUERY_PARAM, None))

		# Unpack OrderBy into filter sytnax
		if self.GET.get(SONADOR_CACHE_ORDER_BY):
			self.order_by = [self.GET.get(SONADOR_CACHE_ORDER_BY)]

		# Retrieve request components: limit, offset, modalities, date filter, and general query parameters
		self.limit = int(self.GET.get('limit', self.limit_default))
		self.offset = int(self.GET.get('offset', self.offset_default))
		self.study_modalities = self.GET.get(DCMHEADER_MODALITIES_IN_STUDY)
		self.study_date_filter = self.GET.get(DCMHEADER_STUDY_DATE)

		logger.warning('DICOMweb request user-uid=%s username="%s" user-permissions="%s" groups=%s: %s' % (
			self.user.pk, getattr(self.user, 'username', None) or '(null)', ','.join(getattr(self.user, 'permissions', [])),
			','.join(str(g.pk) for g in self.groups) if self.groups else '(null)',
			self.dicom_query
		))

	def dcmweb_studyjson(self, cstudy):
		'''	Create a combined dictionary of study and patient metadata

			@returns Wado-RS encoded data dictionary
		'''
		dcm = cstudy.orthanc or {}
		if cstudy.parent:
			dcm.update(cstudy.parent.orthanc or {})

		# Ensure that "ModalitiesInStudy" is populated
		if not dcm.get(DCMHEADER_MODALITIES_IN_STUDY):
			dcm[DCMHEADER_MODALITIES_IN_STUDY] = cstudy.modalities or []

		return json2dcmjson(dcm)

	def get(self, output, uri, request):
		'''	Return list of studies which match the requested parameters
		'''
		with self.sessionmaker() as session:
			try:
				dweb_studies = self.get_studylist(session,
					force_apply_queryfilter=self.force_apply_queryfilter)

			except UnorderableDicomHeader as e:
				# An `OrderBy` the cache is unable to sort on is a bad request rather than a server
				# fault. Report it as one so that a client which asks for an unsupported sort gets a
				# message it can act on instead of a plugin engine error.
				logger.warning('Rejected DICOMweb study list request: %s' % e)
				return self.send_response(json.dumps({
					'Message': str(e),
					SONADOR_CACHE_ORDER_BY: e.header,
				}), status_code=400)

			return self.send_response(json.dumps(
				[self.dcmweb_studyjson(cs) for cs in dweb_studies[self.offset:self.limit+self.offset]],
				cls=SonadorJsonEncoder))


class DicomResourceMixin(ResourceBaseMixin):
	'''	Mixin which provides methods to parse URLs and retrieve resources from the Orthanc database.
	'''
	resource_uid_regex = DICOM_UID_REGEX
	dicom_identifiers_model = DicomIdentifiers

	def get_resource_uid(self, *args, resource_uri=None, **kwargs):
		'''	Retrieve the UID of the DICOM resource from the provided resource URI
		'''
		# Seed the DICOM resource URI by locating the first URL component without alphabetic characters.
		resource_uri = resource_uri or first(self.uri.split('/'), key=lambda s: s.replace('.', '').isnumeric())
		return super().get_resource_uid(*args, resource_uri=resource_uri, **kwargs)		

	def get_resource(self, session, *args, ruid=None, **kwargs):
		'''	Retrieve the DICOM resource instance
		'''
		ruid = ruid or self.get_resource_uid(*args, **kwargs)

		# Retrieve DICOM identifier instance to map to resource
		di = dcmuid_fetch_dicomidentifier_model(
			session, ruid, dicom_identifiers_model=self.dicom_identifiers_model)
		if not di:
			raise ResourceDoesNotExist(
				'Unable to retrieve resource model instance, uid=%s does not exist' % ruid,
				resource_details={ 'type': self.resource_type, 'uid': ruid })

		# Retrieve resource instance
		return di.resource


class DicomUidJsonMixin(object):
	'''	Mixin class which provides methods to add a DICOM UID to Orthanc JSON output.
	'''
	dicom_uid_header = None

	def _init_dicom_json(self, *args, **kwargs):
		self.dicom_uid_header = kwargs.get('dicom_uid_header', self.dicom_uid_header)
		if not self.dicom_uid_header:
			raise ConfigurationError('Unable to initialize %s, invalid DICOM UID header attribute' % type(self).__name__)

	def orthanc_objectjson(self, c, *args, **kwargs):
		'''	Add the DICOM series instance UID to the JSON response
		'''
		cjson = super().orthanc_objectjson(c)
		cjson[self.dicom_uid_header] = self.get_resource_uid(*args, **kwargs)
		return cjson


class CacheStudyDicomWebSeriesMetadataView(DicomResourceMixin, SonadorStudyResourceMixin, OrthancBaseView):
	'''	DICOMweb view able to retrieve metadata for all series in the view study
	'''
	def setup(self, output, uri, request, *args, **kwargs):
		'''	Parse request options
		'''
		request = request or {}
		super().setup(output, uri, request, *args, **kwargs)
		self.init_sonador_resource_mixin(*args, **kwargs)

		# Retrieve URL query parameters from request
		self.GET = self.request.get('get', {})

	def get_study(self, session, *args, **kwargs):
		'''	Retrieve parent study for the view instance
		'''
		# Retrieve resource and object JSON (with extended attributes). Data is retrieved via the DICOM
		# mixin get_resource and get_resource_uid hooks, which trigger a 404 error if the
		# the resource instance does not exist.
		_iserver = self.sonador_manager.get_internal_imageserver()
		return _iserver.study_from_json(
			self.orthanc_resource_json(session, self.get_resource(session, *args, **kwargs), *args, **kwargs))

	def dcmweb_seriesjson(self, sx):
		'''	Convert series DICOM metadata to DICOMweb format
		'''
		return json2dcmjson(copy.deepcopy(sx.dicomdata))

	def get(self, output, uri, request, *args, **kwargs):
		response = kwargs.get('response') or {}

		try:
			
			# Retrieve study instance
			with self.sessionmaker() as session:
				s = self.get_study(session, *args, **kwargs)

				return self.send_response(json.dumps(
					[self.dcmweb_seriesjson(sx) for sx in s.series_collection], 
					cls=SonadorJsonEncoder))	

		# Study does not exist
		except ResourceDoesNotExist as err:
			response.update({
				gcapicodes.ERROR: 'Resource uid=%s does not exist' % self.get_resource_uid(*args, **kwargs) or '(none)',
				gcapicodes.STATUS: gcapicodes.FAIL,
			})
			return self.http404_resource_not_found(response=response)

		# Internal error/exception
		except Exception as err:
			emsg = 'Server error (uid=%s). Error: "%s".' % (self.get_resource_uid(*args, **kwargs), err)
			response.update({ gcapicodes.ERROR: emsg, gcapicodes.STATUS: gcapicodes.FAIL, })
			logger.error('%s\nTraceback: "%s"' % (emsg, traceback.format_exc()))
			
			return self.send_response(json.dumps(response, cls=SonadorJsonEncoder), status_code=500)


class DicomSeriesDicomWebInstanceMetadataView(DicomResourceMixin, SonadorSeriesResourceMixin, OrthancBaseView):
	'''	DICOMweb view able to retrieve metadata for all instances in the view series. Requests retrieve metadata
		from the JSON cache create by the DICOMweb plugin.
	'''
	dicomweb_conf = None
	metadata_cache_attachment = '4301'
	metadata_cache_header = b'\x1f\x8b\x08'

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Parse request options
		'''
		request = request or {}
		super().setup(output, uri, request, *args, **kwargs)
		self.init_sonador_resource_mixin(*args, **kwargs)

		# Parse DICOMweb configuration for view settings
		if not self.dicomweb_conf:
			raise ConfigurationError(('Unable to initialize DicomSeriesDicomWebInstanceMetadataView, '
				+ 'invalid Orthanc DICOMweb configuration') % self.dicomweb_conf)

		# Parse configuration for DICOMweb root and Sonador root
		self.dicomweb_plugin_root = self.dicomweb_conf.get('Root')
		self.dicomweb_root = self.dicomweb_conf.get('SonadorDicomWebRoot') or self.dicomweb_root

		# Ensure that the DICOmweb plugin and public roots are specified to allow for proxy requests.
		if not self.dicomweb_root:
			raise ConfigurationError(('Unable to initialize DicomSeriesDicomWebInstanceMetadataView, '
				+ 'invalid Orthanc DICOMweb root: "%s"') % self.dicomweb_root)
		if not self.dicomweb_plugin_root:
			raise ConfigurationError(('Unable to initialize DicomSeriesDicomWebInstanceMetadataView view, '
				+ 'invalid Orthanc DICOMweb plugin root: "%s"') % self.dicomweb_plugin_root)

		# Retrieve URL query parameters from request
		self.GET = self.request.get('get', {})

	def get_resource_uid(self, *args, resource_uri=None, **kwargs):
		'''	Retrieve the UID of the DICOM resource from the provided resource URI
		'''
		# DICOMweb URLs include study and series UIDs. For that reason, when parsing the 
		# numeric component for the UID read the URL in reverse order
		resource_uri = resource_uri or first(reversed(self.uri.split('/')), key=lambda s: s.replace('.', '').isnumeric())
		return super().get_resource_uid(*args, resource_uri=resource_uri, **kwargs)

	def get_series(self, session, *args, **kwargs):
		'''	Retrieve parent study for the view instance
		'''
		# Retrieve resource and object JSON (with extended attributes). Data is retrieved via the DICOM
		# mixin get_resource and get_resource_uid hooks, which trigger a 404 error if the
		# the resource instance does not exist.
		_iserver = self.sonador_manager.get_internal_imageserver()
		return _iserver.series_from_json(
			self.orthanc_resource_json(session, self.get_resource(session, *args, **kwargs), *args, **kwargs))

	def get(self, output, uri, request, *args, **kwargs):
		response = kwargs.get('response') or {}

		try:
			
			# Retrieve series instance (to ensure that the resource exists and is available within the cache)
			with self.sessionmaker() as session:

				# Initialize series instance from Sonador resource cache and determine if there
				# are any attachments.
				sx = self.get_series(session, *args, **kwargs)
				if not self.metadata_cache_attachment in sx.attachments:
					raise Exception('TODO: Create metadata attachment for series: %s %s' % (
						self.metadata_cache_attachment, sx.attachments
					))

				# Retrieve instance tag data. Tag data is encoded as a gzip compressed JSON string.
				# 4301 attachments from Orthanc include an ASCII encoded header
				# which needs to be stripped prior to decompression. The logic below
				# looks for the binary header value indicating a GZIP string and then pulls
				# all matching content from that position to be decompressed.
				sx_dcm_r = sx.attachment_data(self.metadata_cache_attachment)
				if sx_dcm_idx := sx_dcm_r.content.find(self.metadata_cache_header):

					# Find the compression header, inflate, and load to JSON
					sx_dcm = gzip.decompress(sx_dcm_r.content[sx_dcm_idx:])
					sx_dcm_json = json.loads(sx_dcm)
				
				else:
					sx_dcm = sx_dcm_json = None

				if not sx_dcm_json:
					raise ValueError('Unable to retrieve JSON metadata for series=%s, invalid attachment 4301' % sx.pk)
				
				return self.send_response(json.dumps(sx_dcm_json, cls=SonadorJsonEncoder))

		# Series does not exist
		except ResourceDoesNotExist as err:
			response.update({
				gcapicodes.ERROR: 'Resource uid=%s does not exist' % self.get_resource_uid(*args, **kwargs) or '(none)',
				gcapicodes.STATUS: gcapicodes.FAIL,
			})
			return self.http404_resource_not_found(response=response)

		# Internal error/exception
		except Exception as err:
			emsg = 'Server error (uid=%s). Error: "%s".' % (self.get_resource_uid(*args, **kwargs), err)
			response.update({ gcapicodes.ERROR: emsg, gcapicodes.STATUS: gcapicodes.FAIL, })
			logger.error('%s\nTraceback: "%s"' % (emsg, traceback.format_exc()))
			
			return self.send_response(json.dumps(response, cls=SonadorJsonEncoder), status_code=500)


class InstanceDicomWebFrameView(DicomResourceMixin, OrthancBaseView):
	'''	DICOMweb view able to retrieve frames for an instance. Requests to this endpoint are compatible
		with the ACL policy framework within Sonador.
	'''
	sonador_manager = None
	sessionmaker = None
	resource_cachemodel = CacheInstance

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Initialize DicomWeb frame view instance. Ensure Sonador manager, session maker, resource cachemodel,
			and other required components are available. Capture GET query parameters.
		'''
		if not self.sonador_manager:
			raise ConfigurationError('Unable to initialize InstanceDicomWebFrameView, invalid Sonador manager instance')
		if not self.sessionmaker:
			raise ConfigurationError('Unable to initialize InstanceDicomWebFrameView, invalid session maker instance')

		if not self.resource_cachemodel:
			raise ConfigurationError('Unable to initialize InstanceDicomWebFrameView, invalid resource cache model')
		
		self.init_resource_mixin(
			resource_type=self.resource_cachemodel.type, resource_code=self.resource_cachemodel.code)

		super().setup(output, uri, request, *args, **kwargs)

		self.GET = request.get('get', {})

	def get_resource_uid(self, *args, resource_uri=None, **kwargs):
		'''	Retrieve the UID of the DICOM resource from the provided resource URI
		'''
		# Instance DICOMweb URLs include study, series and instance UIDs. For that reason, when parsing the 
		# numeric component for the UID read the URL in reverse order
		resource_uri = resource_uri or first(tuple(reversed(self.uri.split('/')))[1:], key=lambda s: s.replace('.', '').isnumeric())
		return super().get_resource_uid(*args, resource_uri=resource_uri, **kwargs)

	def get_frame_number(self, *args, **kwargs):
		'''	Retrieve frame number from the URL
		'''
		return int(self.uri.split('/')[-1])

	def get(self, output, uri, request, *args, **kwargs):
		response = kwargs.get('response', {})

		try:

			# Retrieve DICOM instance
			with self.sessionmaker() as session:

				# Retrieve DICOM instance
				r = self.get_resource(session, *args, **kwargs)
				_iserver = self.sonador_manager.get_internal_imageserver()
				dcm = _iserver.get_dcm_instance(r.publicid)

				# Parse DICOM file content
				dcmfile = dcm.dcmfile()
				frame_idx = self.get_frame_number(*args, **kwargs)

				# Ensure that the requested frame is within the DICOM instance pixel data array
				if (frame_idx > 1 and len(dcmfile.pixel_array.shape) == 2) \
					or (frame_idx > dcmfile.pixel_array.shape[0]):
					raise ValueError('Invalid request. Requested frame=%s for instance=%s is outside of DICOM pixel data array. DCM dimensions: %s' % (
							frame_idx, dcm.pk, dcmfile.pixel_array.shape,
						))

				# Single frame instance
				if (frame_idx == 1 and len(dcmfile.pixel_array.shape)) == 2:
					dcm_pixels = dcmfile.pixel_array

				else:
					dcm_pixels = dcmfile.pixel_array[frame_idx]
				
				return self.send_response(dcm_pixels.tobytes(), mtype=DCM_CONTENT_TYPE)

		# Instance does not exist
		except ResourceDoesNotExist as err:
			response.update({
				gcapicodes.ERROR: 'Resource uid=%s does not exist' % self.get_resource_uid(*args, **kwargs) or '(none)',
				gcapicodes.STATUS: gcapicodes.FAIL,
			})
			return self.http404_resource_not_found(response=response)

		# Internal error/exception
		except Exception as err:
			emsg = 'Server error (uid=%s). Error: "%s".' % (self.get_resource_uid(*args, **kwargs), err)
			response.update({ gcapicodes.ERROR: emsg, gcapicodes.STATUS: gcapicodes.FAIL, })
			logger.error('%s\nTraceback: "%s"' % (emsg, traceback.format_exc()))
			
			return self.send_response(json.dumps(response, cls=SonadorJsonEncoder), status_code=500)


class InstanceDicomWebFileView(DicomResourceMixin, OrthancBaseView):
	'''	DICOMweb view able to retrieve DICOM instances. Requests to this endpoint are compatible
		with the ACL policy framework within Sonador.
	'''
	sonador_manager = None
	sessionmaker = None
	resource_cachemodel = CacheInstance

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Initialize DICOMweb file view instance. Ensure Sonador Manager, session maker, resource cachemodel,
			and other required components are available. Capture GET query parameters.
		'''
		if not self.sonador_manager:
			raise ConfigurationError('Unable to initialize InstanceDicomWebFileView, invalid Sonador manager instance ')
		if not self.sessionmaker:
			raise ConfigurationError('Unable to initialize InstanceDicomWebFileView, invalid session maker instance')

		if not self.resource_cachemodel:
			raise ConfigurationError('Unable to initialize InstanceDicomWebFileView, invalid resource cache model')

		self.init_resource_mixin(
			resource_type=self.resource_cachemodel.type, resource_code=self.resource_cachemodel.code)

		super().setup(output, uri, request, *args, **kwargs)

		self.GET = request.get('get', {})

	def get_resource_uid(self, *args, resource_uri=None, **kwargs):
		'''	Retrieve the UID of the DICOM resource from the provided resource URI
		'''
		# Instance DICOMweb URLs include study, series and instance UIDs. For that reason, when parsing the 
		# numeric component for the UID read the URL in reverse order
		resource_uri = resource_uri or first(reversed(self.uri.split('/')), key=lambda s: s.replace('.', '').isnumeric())
		return super().get_resource_uid(*args, resource_uri=resource_uri, **kwargs)

	def get(self, output, uri, request, *args, **kwargs):
		response = kwargs.get('response', {})

		try:

			# Retrieve DICOM instance
			with self.sessionmaker() as session:

				# Retrieve DICOM instance
				r = self.get_resource(session, *args, **kwargs)
				_iserver = self.sonador_manager.get_internal_imageserver()
				dcm = _iserver.get_dcm_instance(r.publicid)

				# Parse DICOM file content
				dcmfile = dcm.dcmfile()

				# Ensure that DCM file includes all required components Part10 components for rendering
				dcmfile, _part10_backfill = dcm_part10_backfill(dcmfile)
				if _part10_backfill:

					# Save to output stream and set seek position at start
					_stream = BytesIO()
					dcmfile.save_as(_stream)

				else:
					_stream = dcmfile.raw

				# Set stream seek position to 0
				_stream.seek(0)

				# Determine how response needs to be encoded
				if self.request_headers.get('accept') and 'multipart/related' in self.request_headers.get('accept').lower():

					# Send multi-part encoded response
					_multipart = dcmweb_encode_multipart_single(DCM_DICOM_CONTENT_TYPE, _stream.read(), uri)
					
					return self.send_response(_multipart.message, headers={
						'Content-Type': 'multipart/related; type=%s; boundary=%s' % (DCM_DICOM_CONTENT_TYPE, _multipart.boundary),
						'Cache-Control':  'no-store',
					})

				raise Exception('Determine how file should be added to response: %s' % self.request_headers)
				return self.send_response(_stream.read(), mtype='application/dicom')

		# Instance does not exist
		except ResourceDoesNotExist as err:
			response.update({
				gcapicodes.ERROR: 'Resource uid=%s does not exist' % self.get_resource_uid(*args, **kwargs) or '(none)',
				gcapicodes.STATUS: gcapicodes.FAIL,
			})
			return self.http404_resource_not_found(response=response)

		# Internal error/exception
		except Exception as err:
			emsg = 'Server error (uid=%s). Error: "%s".' % (self.get_resource_uid(*args, **kwargs), err)
			response.update({ gcapicodes.ERROR: emsg, gcapicodes.STATUS: gcapicodes.FAIL, })
			logger.error('%s\nTraceback: "%s"' % (emsg, traceback.format_exc()))
			
			return self.send_response(json.dumps(response, cls=SonadorJsonEncoder), status_code=500)


class DicomWebRedirectView(RedirectView):
	'''	View instance which can be used to redirect requests from one DICOMweb tree to
		another. This is used within the Orthanc Sonador Cloud plugin to override
		Orthanc DICOMweb routes (https://www.orthanc-server.com/static.php?page=dicomweb)
		while still preserving the ability to forward requests to the Orthanc provided
		endpoints where desired.
	'''
	dicomweb_root = None
	dicomweb_forward_root = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)

		# Ensure required components are present
		if not self.dicomweb_root:
			raise Exception('Unable to initialize DicomWebRedirectView, invalid DICOMweb root: "%s' % self.dicomweb_root)
		if not self.dicomweb_forward_root:
			raise Exception('Unable to initailzie DicomWebRedirectView, invalid DICOMweb root base to forward traffic to "%s"' 
				% self.dicomweb_root)

	def rewrite_url(self, output, uri, request, *args,  **kwargs):
		'''	Replace the DICOMweb root with the DICOMweb forward root
		'''
		return uri.replace(self.dicomweb_root, self.dicomweb_forward_root, 1)


def init_dcmweb_system_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb system endpoints. This method is called before all other DICOMweb endpoint
		registration methods AND before the system has completed its system initialization. It is
		used to register redirect methods for DICOMweb remote forwarding within the system configuration.
	'''
	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})
	dicomweb_plugin_root = dicomweb_conf.get('Root')
	dicomweb_root = dicomweb_conf.get('SonadorDicomWebRoot') or dicomweb_plugin_root
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize Study list endpoint, invalid DICOMweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	if not dicomweb_conf.get('Enable'):
		raise ConfigurationError('Invalid Orthanc configuration. Sonador requires that the Orthanc DICOMweb plugin be enabled.')

	# Init DICOMweb redirect endpoints
	if dicomweb_plugin_root and dicomweb_plugin_root != dicomweb_root:
		orthanc.LogWarning('Enable DICOMweb system redirect views from root="%s" to "%s" for DICOMweb remotes' 
			% (dicomweb_root, dicomweb_plugin_root))

		# Redirect DICOMweb system endpoints to plugin root
		# * /dicom-web/servers : DICOMmweb server registration endpoint
		# * /dicom-web/servers/{ server-uid } : DICOMweb server update endpoint
		dcmweb_server_root = r'/%s/servers' % dicomweb_root.replace('/', '')
		orthanc.LogWarning('Enable DICOMmweb server management endpoint redirect: endpoint="%s" plugin-endpoint="%s"' % (
			dcmweb_server_root, r'/%s/servers' % dicomweb_plugin_root.replace('/', ''),
		))
		orthanc.RegisterRestCallback(dcmweb_server_root,
			DicomWebRedirectView.as_view(dicomweb_root=dicomweb_root, dicomweb_forward_root=dicomweb_plugin_root,
				allow_get=True, allow_post=True))

		dcmweb_server_update = r'%s/(.+)' % dcmweb_server_root
		orthanc.LogWarning('Enable DICOMmweb server REST endpoint redirect: endpoint="%s" plugin-endpoint="%s"' % (
			dcmweb_server_update, r'/%s/servers/.+)' % dicomweb_plugin_root.replace('/', ''),
		))
		orthanc.RegisterRestCallback(dcmweb_server_update,
			DicomWebRedirectView.as_view(dicomweb_root=dicomweb_root, dicomweb_forward_root=dicomweb_plugin_root,
				allow_get=True, allow_put=True, allow_delete=True))


def init_cached_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb endpoints which utilize the Sonador Resource cache
	'''
	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})
	dcm_privatetags = orthanc_conf.get(SONADOR_CONF_PRIVATE_TAGS, {})

	dicomweb_plugin_root = dicomweb_conf.get('Root')
	dicomweb_root = dicomweb_conf.get('SonadorDicomWebRoot') or dicomweb_plugin_root
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize Study list endpoint, invalid DICOMweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	dcmweb_study_query = posixpath.join(dicomweb_root, 'studies')
	orthanc.LogWarning(('Enabling cached DICOMweb study list at endpoint "%s". Only resources indexed in cache '
			'will be included in searches and data in ExtraMainDicomTags will be incorporated in responses.')
		% dcmweb_study_query)

	# Initialize DICOMweb study query interface
	orthanc.RegisterRestCallback(dcmweb_study_query,
		CacheStudyDicomWebListView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, cache_dicomtags=orthanc_maindicom_tags(orthanc_conf), dcm_privatetags=dcm_privatetags))

	# Init DICOMweb redirect endpoints
	if dicomweb_plugin_root and dicomweb_plugin_root != dicomweb_root:

		# DICOMweb cache endpoints
		dcmweb_study_metadata_series_root = posixpath.join(dcmweb_study_query, r'(\d+(\.\d+)+)/series')
		orthanc.LogWarning('Enabling DICOMweb cache endpoint for study/series metadata: %s' % dcmweb_study_metadata_series_root)
		orthanc.RegisterRestCallback(dcmweb_study_metadata_series_root,
			CacheStudyDicomWebSeriesMetadataView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))

		# Process study-level metadata requests (series batch) and series metadata requests with Sonador specific views
		# to allow for integration with ACL policies.
		# * /dicom-web/{ study-uid }/series/{ series-uid }/metadata : series metadata requests
		# * /dicom-web/{ study-uid }/series/{ series-uid }/instances/{ instance-uid }/frames/{ frame-number }
		
		dcmweb_series_metadata_url = r'%s/(\d+(\.\d+)+)/metadata' % dcmweb_study_metadata_series_root
		orthanc.LogWarning('Enable DICOMweb series metadata endpoint: %s' % dcmweb_series_metadata_url)
		orthanc.RegisterRestCallback(dcmweb_series_metadata_url,
			DicomSeriesDicomWebInstanceMetadataView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession,
				dicomweb_conf=dicomweb_conf))

		dcmweb_instance_frame_url = r'%s/(\d+(\.\d+)+)/instances/(\d+(\.\d+)+)/frames/(\d+)' % dcmweb_study_metadata_series_root
		orthanc.LogWarning('Enable DICOMweb instance frame endpoint: %s' % dcmweb_instance_frame_url)
		orthanc.RegisterRestCallback(dcmweb_instance_frame_url,
			InstanceDicomWebFrameView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))

		# Redirect instance level requests
		dcmweb_instance_file_url = r'%s/(\d+(\.\d+)+)/instances/(\d+(\.\d+)+)' % dcmweb_study_metadata_series_root
		orthanc.LogWarning('Enable DICOMweb instance endpoint: %s' % dcmweb_instance_file_url)
		orthanc.RegisterRestCallback(dcmweb_instance_file_url,
			InstanceDicomWebFileView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))

	
def init_download_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb endpoints for zip archive download
	'''
	from .download import StudyDICOMDownloadView, SeriesDICOMDownloadView

	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})

	dicomweb_plugin_root = dicomweb_conf.get('Root')
	dicomweb_root = dicomweb_conf.get('SonadorDicomWebRoot') or dicomweb_plugin_root
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize DICOmweb download endpoints, invalid DICOMweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	# Initialize DICOMweb download endpoitns
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/archive'),
		StudyDICOMDownloadView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/archive'),
		SeriesDICOMDownloadView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))


def init_manage_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb resource management endpoints. "manage" is a generic management
		namespace: this registration delivers removal (DELETE), and later management
		operations extend the same views rather than needing a new route.
	'''
	from .manage import StudyDICOMManageView, SeriesDICOMManageView

	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})

	dicomweb_plugin_root = dicomweb_conf.get('Root')
	dicomweb_root = dicomweb_conf.get('SonadorDicomWebRoot') or dicomweb_plugin_root
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize DICOMweb management endpoints, invalid DICOMweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	# Initialize DICOMweb resource management endpoints
	manage_study_dicomweb_url = posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/manage')
	orthanc.LogWarning('Enabling DICOMweb extension: study management %s' % manage_study_dicomweb_url)
	orthanc.RegisterRestCallback(manage_study_dicomweb_url,
		StudyDICOMManageView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))

	manage_series_dicomweb_url = posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/manage')
	orthanc.LogWarning('Enabling DICOMweb extension: series management %s' % manage_series_dicomweb_url)
	orthanc.RegisterRestCallback(manage_series_dicomweb_url,
		SeriesDICOMManageView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))


def init_ext_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb extension endpoints
	'''
	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})

	dicomweb_plugin_root = dicomweb_conf.get('Root')
	dicomweb_root = dicomweb_conf.get('SonadorDicomWebRoot') or dicomweb_plugin_root
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize DICOmweb extension endpoints, invalid DICOMweb configuration. '
			+ 'No DICOMweb root defined in configuration.')
	
	if getattr(sonador_manager, "kafka_producer", None) and getattr(sonador_manager.kafka_producer, "topic", None):
		kafka_topic = sonador_manager.kafka_producer.topic
	else:
		kafka_topic = None

	from .comments import CommentSeriesDICOMManagementView, CommentSeriesDICOMRestView, CommentStudyDICOMManagementView, CommentStudyDICOMRestView

	# Series DICOMweb comment endpoints
	comments_series_dicomweb_url = posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/comments')
	orthanc.LogWarning('Enabling DICOMweb extension: series comments %s' % comments_series_dicomweb_url)

	orthanc.RegisterRestCallback(comments_series_dicomweb_url, CommentSeriesDICOMManagementView.as_view(
		sonador_manager=sonador_manager, sessionmaker=OrthancSession, kafka_topic=kafka_topic))
	orthanc.RegisterRestCallback(
		posixpath.join(dicomweb_root,
			r'series/(\d+(\.\d+)+)/comments/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		CommentSeriesDICOMRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession, kafka_topic=kafka_topic))

	# Study DICOMweb comment endpoints
	comments_study_dicomweb_url = posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/comments')
	orthanc.LogWarning('Enabling DICOMweb extension: study comments %s' % comments_study_dicomweb_url)

	orthanc.RegisterRestCallback(comments_study_dicomweb_url, CommentStudyDICOMManagementView.as_view(
		sonador_manager=sonador_manager, sessionmaker=OrthancSession, kafka_topic=kafka_topic))
	orthanc.RegisterRestCallback(
		posixpath.join(dicomweb_root,
			r'studies/(\d+(\.\d+)+)/comments/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		CommentStudyDICOMRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession, kafka_topic=kafka_topic))


def init_distortionfilter_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb distortion filter endpoints
	'''
	from .distortionfilter import DistortionFilterDeviceManagementView, DistortionFilterDeviceRestView, DeviceDistortionDICOMView

	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})

	dicomweb_plugin_root = dicomweb_conf.get('Root')
	dicomweb_root = dicomweb_conf.get('SonadorDicomWebRoot') or dicomweb_plugin_root
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize DICOmweb extension endpoints, invalid DICOmweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	orthanc.LogWarning('Enabling DICOMweb distortion filter endpoints: %s' % posixpath.join(dicomweb_root, 'distortion-filter'))

	# Distortion Filter: Device Management API
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'groups/[0-9]+/distortion-filter/devices'),
		DistortionFilterDeviceManagementView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, 
			r'groups/[0-9]+/distortion-filter/devices/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		DistortionFilterDeviceRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))

	# Distortion Filter: Apply Filter
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'groups/[0-9]+/distortion-filter/(\d+(\.\d+)+)'),
		DeviceDistortionDICOMView.as_view(
			sonador_manager=sonador_manager, sessionmaker=OrthancSession, resource_cachemodel=CacheStudy,
			dicom_uid_header=DCMHEADER_STUDY_INSTANCE_UID))


def init_worklist_endpints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb worklist endpoints
	'''
	# Worklist Views
	from ..worklist.web import StudyReviewerWorklistItemDICOMManagementView, StudyReviewerWorklistItemDICOMRestView, \
		StudyReviewerWorklistItemDICOMListView

	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})
	dcm_privatetags = orthanc_conf.get(SONADOR_CONF_PRIVATE_TAGS, {})

	dicomweb_plugin_root = dicomweb_conf.get('Root')
	dicomweb_root = dicomweb_conf.get('SonadorDicomWebRoot') or dicomweb_plugin_root
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize Study list endpoint, invalid DICOMweb configuration. '
			+ 'No DICOMweb root defined in configuration.')
	
	if getattr(sonador_manager, "kafka_producer", None) and getattr(sonador_manager.kafka_producer, "topic", None):
		kafka_topic = sonador_manager.kafka_producer.topic
	else:
		kafka_topic = None

	dicomweb_worklist_study_management_url = posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/worklists')
	orthanc.LogWarning('Enabling DICOMweb worklist management endpoints: %s' % dicomweb_worklist_study_management_url)
	
	# Reviewer Worklist Item Management and REST Views
	orthanc.RegisterRestCallback(dicomweb_worklist_study_management_url,
		StudyReviewerWorklistItemDICOMManagementView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession, kafka_topic=kafka_topic))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/worklists/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		StudyReviewerWorklistItemDICOMRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession, kafka_topic=kafka_topic))

	dicomweb_worklist_study_url = posixpath.join(dicomweb_root, 'worklist/studies')
	orthanc.LogWarning('Enable DICOMweb study review worklist endpoint: %s' % dicomweb_worklist_study_url)

	# Reviewer Worklist Study View
	orthanc.RegisterRestCallback(dicomweb_worklist_study_url,
		StudyReviewerWorklistItemDICOMListView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession, 
			cache_dicomtags=orthanc_maindicom_tags(orthanc_conf), dcm_privatetags=dcm_privatetags))
	

def init_auth_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb ACL REST endpoints
	'''
	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})

	dicomweb_plugin_root = dicomweb_conf.get('Root')
	dicomweb_root = dicomweb_conf.get('SonadorDicomWebRoot') or dicomweb_plugin_root
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize DICOmweb ACL endpoints, invalid DICOmweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	# ACL Models, Forms, and Views
	from ..db.auth import UserPatientAuth, GroupPatientAuth, UserStudyAuth, GroupStudyAuth, \
		UserSeriesAuth, GroupSeriesAuth
	from ..validation.auth import AuthValidationForm, AuthExtendedValidationForm, SonadorResourceAuthorizationRequest, \
		UserAclValidationForm, UserAclExtendedValidationForm, GroupAclValidationForm, GroupAclExtendedValidationForm
	from ..auth.web import AuthDICOMManagementView, AuthDICOMRestView, AuthDICOMResourcePermissionLookupView

	orthanc.LogWarning('Enabling DICOMweb ACL endpoints')
	
	
	# Study ACL endpoints

	# User endpoints
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/acl/user'),
		AuthDICOMManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, model=UserStudyAuth,
			modelform=UserAclExtendedValidationForm, dicom_uid_header=DCMHEADER_STUDY_INSTANCE_UID))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/acl/user/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		AuthDICOMRestView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, model=UserStudyAuth,
			modelform=UserAclExtendedValidationForm, dicom_uid_header=DCMHEADER_STUDY_INSTANCE_UID))

	# Group endpoints
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/acl/group'),
		AuthDICOMManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, model=GroupStudyAuth,
			modelform=GroupAclExtendedValidationForm, dicom_uid_header=DCMHEADER_STUDY_INSTANCE_UID))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/acl/group/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		AuthDICOMRestView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, model=GroupStudyAuth,
			modelform=GroupAclExtendedValidationForm, dicom_uid_header=DCMHEADER_STUDY_INSTANCE_UID))

	# Resource ACL/permission lookup
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/resource-acl'),
		AuthDICOMResourcePermissionLookupView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, dicom_uid_header=DCMHEADER_STUDY_INSTANCE_UID))

	
	# Series ACL endpoints

	# User endpoints
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/acl/user'),
		AuthDICOMManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, model=UserSeriesAuth,
			modelform=UserAclExtendedValidationForm, dicom_uid_header=DCMHEADER_SERIES_INSTANCE_UID))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/acl/user/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		AuthDICOMRestView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, model=UserSeriesAuth,
			modelform=UserAclExtendedValidationForm, dicom_uid_header=DCMHEADER_SERIES_INSTANCE_UID))

	# Group endpoints
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/acl/group'),
		AuthDICOMManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, model=GroupSeriesAuth,
			modelform=GroupAclExtendedValidationForm, dicom_uid_header=DCMHEADER_SERIES_INSTANCE_UID))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/acl/group/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		AuthDICOMRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, 
			model=GroupSeriesAuth, modelform=GroupAclExtendedValidationForm, dicom_uid_header=DCMHEADER_SERIES_INSTANCE_UID))

	# Resource ACL/permission lookup
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/resource-acl'),
		AuthDICOMResourcePermissionLookupView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, dicom_uid_header=DCMHEADER_SERIES_INSTANCE_UID))
