'''	Orthanc views providing DICOMweb compatability
'''
import posixpath, pydicom, logging, json, copy, datetime
import orthanc

from sqlalchemy.orm import joinedload

from client.errors import ConfigurationError, ResourceDoesNotExist
from client.utils.object import omit, pick
from client.utils.general import first

from sonador.apisettings import \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES,  \
	DICOM_UID_REGEX, DCMHEADER_STUDY_INSTANCE_UID, DCMHEADER_SERIES_INSTANCE_UID, DCMHEADER_MODALITIES_IN_STUDY, \
	DCMHEADER_MODALITY, DCMHEADER_STUDY_DATE
from sonador.imaging.helpers.conversion import json2dcmjson
from sonador.serialization import dcm_str2date, SonadorJsonEncoder

from .. import apisettings as sonador_api
from ..apisettings import ORTHANC_CONFIG_SECTION_DICOMWEB, ORTHANC_CONFIG_SECTION_POSTGRES, \
	ORTHANC_CONFIG_SECTION_EXTRADICOMTAGS, ORTHANC_MAINDICOM_TAGS_DEFAULT, SONADOR_CONF_PRIVATE_TAGS
from ..helpers import orthanc_maindicom_tags
from ..db.internal import Resource, DicomIdentifiers
from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.helpers import dcmquery2psqlregex
from ..dcmquery.auth import StudyResourceAclMixin

from .base import OrthancBaseView
from .study import CacheStudyListBaseView
from .cache import ResourceBaseMixin
from .secure_user import UserContextMixin
from .secure_search import SecureResourceQueryViewMixin

from ..dcmquery.auth import StudyResourceAclMixin

from ..dcmquery.auth import StudyResourceAclMixin

logger = logging.getLogger(__name__)


class CacheStudyDicomWebListView(SecureResourceQueryViewMixin, StudyResourceAclMixin, UserContextMixin, CacheStudyListBaseView):
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
			dweb_studies = self.get_studylist(session)

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
		return super().get_resource_uid(*args, resource_uri=resource_uri, **kwargs)

	def get_resource(self, session, *args, ruid=None, **kwargs):
		'''	Retrieve the DICOM resource instance
		'''
		ruid = ruid or self.get_resource_uid(*args, **kwargs)

		# Retrieve DICOM identifier instance to map to resource
		di = session.query(self.dicom_identifiers_model) \
			.options(joinedload(self.dicom_identifiers_model.resource)) \
			.filter_by(value=ruid).first()
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


def init_cached_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb endpoints which utilize the Sonador Resource cache
	'''
	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})
	dicomweb_root = dicomweb_conf.get('Root')
	dcm_privatetags = orthanc_conf.get(SONADOR_CONF_PRIVATE_TAGS, {})
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize Study list endpoint, invalid DICOMweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	orthanc.LogWarning(('Enabling cached DICOMweb study list at endpoint "%s". Only resources indexed in cache '
			'will be included in searches and data in ExtraMainDicomTags will be incorporated in responses.')
		% posixpath.join(dicomweb_root, 'studies'))

	# Initialize DICOMweb study query interface
	orthanc.RegisterRestCallback(
		posixpath.join(dicomweb_root, 'studies'),
		CacheStudyDicomWebListView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, cache_dicomtags=orthanc_maindicom_tags(orthanc_conf), dcm_privatetags=dcm_privatetags))


def init_ext_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb extension endpoints
	'''
	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})
	dicomweb_root = dicomweb_conf.get('Root')
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize DICOmweb extension endpoints, invalid DICOmweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	from .comments import CommentSeriesDICOMManagementView, CommentSeriesDICOMRestView, CommentStudyDICOMManagementView, CommentStudyDICOMRestView

	comments_series_dicomweb_url = posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/comments')
	orthanc.LogWarning('Enabling DICOMweb extension: series comments %s' % comments_series_dicomweb_url)

	orthanc.RegisterRestCallback(comments_series_dicomweb_url, CommentSeriesDICOMManagementView.as_view(
		sonador_manager=sonador_manager, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(
		posixpath.join(dicomweb_root,
			r'series/(\d+(\.\d+)+)/comments/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		CommentSeriesDICOMRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))


	comments_study_dicomweb_url = posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/comments')
	orthanc.LogWarning('Enabling DICOMweb extension: study comments %s' % comments_study_dicomweb_url)

	orthanc.RegisterRestCallback(comments_study_dicomweb_url, CommentStudyDICOMManagementView.as_view(
		sonador_manager=sonador_manager, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(
		posixpath.join(dicomweb_root,
			r'studies/(\d+(\.\d+)+)/comments/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		CommentStudyDICOMRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))


def init_distortionfilter_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb distortion filter endpoints
	'''
	from .distortionfilter import DistortionFilterDeviceManagementView, DistortionFilterDeviceRestView, DeviceDistortionDICOMView

	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})
	dicomweb_root = dicomweb_conf.get('Root')
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize DICOmweb extension endpoints, invalid DICOmweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	orthanc.LogWarning('Enabling DICOMweb distortion filter endpoints: %s' % posixpath.join(dicomweb_root, 'distortion-filter'))

	# Distortion Filter: Device Management API
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'distortion-filter/devices'),
		DistortionFilterDeviceManagementView.as_view(sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'distortion-filter/devices/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		DistortionFilterDeviceRestView.as_view(sessionmaker=OrthancSession))

	# Distortion Filter: Apply Filter
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'distortion-filter/(\d+(\.\d+)+)'),
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
	dicomweb_root = dicomweb_conf.get('Root')
	dcm_privatetags = orthanc_conf.get(SONADOR_CONF_PRIVATE_TAGS, {})
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize Study list endpoint, invalid DICOMweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	dicomweb_worklist_study_management_url = posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/worklists')
	orthanc.LogWarning('Enabling DICOMweb worklist management endpoints: %s' % dicomweb_worklist_study_management_url)
	
	# Reviewer Worklist Item Management and REST Views
	orthanc.RegisterRestCallback(dicomweb_worklist_study_management_url,
		StudyReviewerWorklistItemDICOMManagementView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/worklists/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		StudyReviewerWorklistItemDICOMRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))

	dicomweb_worklist_study_url = posixpath.join(dicomweb_root, 'worklist/studies')
	orthanc.LogWarning('Enable DICOMweb study review worklist endpoint: %s' % dicomweb_worklist_study_url)

	# Reviewer Worklist Study View
	orthanc.RegisterRestCallback(dicomweb_worklist_study_url,
		StudyReviewerWorklistItemDICOMListView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession, 
			cache_dicomtags=orthanc_maindicom_tags(orthanc_conf), dcm_privatetags=dcm_privatetags))
	
	
	

def init_worklist_endpints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb worklist endpoints
	'''
	# Worklist Views
	from ..worklist.web import StudyReviewerWorklistItemDICOMManagementView, StudyReviewerWorklistItemDICOMRestView

	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})
	dicomweb_root = dicomweb_conf.get('Root')
	dcm_privatetags = orthanc_conf.get(SONADOR_CONF_PRIVATE_TAGS, {})
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize Study list endpoint, invalid DICOMweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	orthanc.LogWarning('Enabling DICOMweb worklist endpoints')
	
	# Reviewer Worklist Item endpoints
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/worklists'),
		StudyReviewerWorklistItemDICOMManagementView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/worklists/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		StudyReviewerWorklistItemDICOMRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))

def init_tag_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb extension endpoints
	'''
	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})
	dicomweb_root = dicomweb_conf.get('Root')
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize DICOmweb extension endpoints, invalid DICOmweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	orthanc.LogWarning('Enabling DICOMweb tag endpoints')

	from ..web.tag import SeriesTagItemDICOMManagementView, SeriesTagItemDICOMRestView
	
	# Reviewer Worklist Item endpoints
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/tag'),
		SeriesTagItemDICOMManagementView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/tag/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		SeriesTagItemDICOMRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))
	

def init_auth_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb ACL REST endpoints
	'''
	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})
	dicomweb_root = dicomweb_conf.get('Root')
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize DICOmweb ACL endpoints, invalid DICOmweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	# ACL Models, Forms, and Views
	from ..db.auth import UserPatientAuth, GroupPatientAuth, UserStudyAuth, GroupStudyAuth, \
		UserSeriesAuth, GroupSeriesAuth
	from ..validation.auth import AuthValidationForm, AuthExtendedValidationForm, SonadorResourceAuthorizationRequest, \
		UserAclValidationForm, UserAclExtendedValidationForm, GroupAclValidationForm, GroupAclExtendedValidationForm
	from ..auth.web import AuthDICOMManagementView, AuthDICOMRestView

	orthanc.LogWarning('Enabling DICOMweb ACL endpoints')
	
	# Study ACL endpoints
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/acl/user'),
		AuthDICOMManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, model=UserStudyAuth,
			modelform=UserAclValidationForm, dicom_uid_header=DCMHEADER_STUDY_INSTANCE_UID))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/acl/user/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		AuthDICOMRestView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, model=UserStudyAuth,
			modelform=UserAclValidationForm, dicom_uid_header=DCMHEADER_STUDY_INSTANCE_UID))

	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/acl/group'),
		AuthDICOMManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, model=GroupStudyAuth,
			modelform=GroupAclValidationForm, dicom_uid_header=DCMHEADER_STUDY_INSTANCE_UID))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'studies/(\d+(\.\d+)+)/acl/group/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		AuthDICOMRestView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, model=GroupStudyAuth,
			modelform=GroupAclValidationForm, dicom_uid_header=DCMHEADER_STUDY_INSTANCE_UID))


	# Series ACL endpoints
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/acl/user'),
		AuthDICOMManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, model=UserSeriesAuth,
			modelform=UserAclExtendedValidationForm, dicom_uid_header=DCMHEADER_SERIES_INSTANCE_UID))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/acl/user/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		AuthDICOMRestView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, model=UserSeriesAuth,
			modelform=UserAclExtendedValidationForm, dicom_uid_header=DCMHEADER_SERIES_INSTANCE_UID))

	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/acl/group'),
		AuthDICOMManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, model=GroupSeriesAuth,
			modelform=GroupAclExtendedValidationForm, dicom_uid_header=DCMHEADER_SERIES_INSTANCE_UID))
	orthanc.RegisterRestCallback(posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/acl/group/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		AuthDICOMRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, 
			model=GroupSeriesAuth, modelform=GroupAclExtendedValidationForm, dicom_uid_header=DCMHEADER_SERIES_INSTANCE_UID))
