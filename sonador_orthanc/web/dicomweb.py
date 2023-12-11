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
	DICOM_UID_REGEX, DCMHEADER_MODALITIES_IN_STUDY, DCMHEADER_MODALITY, DCMHEADER_STUDY_DATE
from sonador.imaging.helpers.conversion import json2dcmjson
from sonador.serialization import dcm_str2date

from ..apisettings import ORTHANC_CONFIG_SECTION_DICOMWEB, ORTHANC_CONFIG_SECTION_POSTGRES, \
	ORTHANC_CONFIG_SECTION_EXTRADICOMTAGS, ORTHANC_MAINDICOM_TAGS_DEFAULT, SONADOR_CONF_PRIVATE_TAGS
from ..helpers import orthanc_maindicom_tags
from ..db.internal import Resource, DicomIdentifiers
from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.helpers import dcmquery2psqlregex

from .base import OrthancBaseView
from .study import CacheStudyListBaseView
from .cache import ResourceBaseMixin

logger = logging.getLogger(__name__)


class CacheStudyDicomWebListView(CacheStudyListBaseView):
	'''	DICOMweb REST study list endpoint which is able to use the Sonador database cache.
	'''
	limit_default = 100
	offset_default = 0

	def setup(self, output, uri, request, *args, **kwargs):

		# Set GET request and general query parameters
		request = request or {}
		self.dicom_query = omit(request.get('get', {}),
			('limit', 'offset', 'fuzzymatching', 'includefield', DCMHEADER_MODALITIES_IN_STUDY, DCMHEADER_STUDY_DATE))

		super().setup(output, uri, request)

		# Retrieve URL query parameters from request, pull DICOM query parameters from GET
		self.GET = self.request.get('get', {})

		# Retrieve request components: limit, offset, modalities, date filter, and general query parameters
		self.limit = int(self.GET.get('limit', self.limit_default))
		self.offset = int(self.GET.get('offset', self.offset_default))
		self.study_modalities = self.GET.get(DCMHEADER_MODALITIES_IN_STUDY)
		self.study_date_filter = self.GET.get(DCMHEADER_STUDY_DATE)

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
				[self.dcmweb_studyjson(cs) for cs in dweb_studies[self.offset:self.limit+self.offset]]))


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
		di = session.query(self.dicom_identifiers_model) \
			.options(joinedload(self.dicom_identifiers_model.resource)) \
			.filter_by(value=ruid).first()
		if not di:
			raise ResourceDoesNotExist(
				'Unable to retrieve resource model instance, uid=%s does not exist' % ruid,
				resource_details={ 'type': self.resource_type, 'uid': ruid })

		# Retrieve resource instance
		return di.resource


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
		CacheStudyDicomWebListView.as_view(
			sessionmaker=OrthancSession, cache_dicomtags=orthanc_maindicom_tags(orthanc_conf), dcm_privatetags=dcm_privatetags))


def init_ext_endpoints(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize DICOMweb extension endpoints 
	'''
	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})
	dicomweb_root = dicomweb_conf.get('Root')
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize Study list endpoint, invalid DICOmweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	from .comments import CommentSeriesDICOMManagementView, CommentSeriesDICOMRestView

	comments_dicomweb_url = posixpath.join(dicomweb_root, r'series/(\d+(\.\d+)+)/comments')
	orthanc.LogWarning('Enabling DICOMweb extesion: comments (%s)' % comments_dicomweb_url)

	orthanc.RegisterRestCallback(comments_dicomweb_url, CommentSeriesDICOMManagementView.as_view(sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(
		posixpath.join(dicomweb_root,
			r'series/(\d+(\.\d+)+)/comments/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'),
		CommentSeriesDICOMRestView.as_view(sessionmaker=OrthancSession))