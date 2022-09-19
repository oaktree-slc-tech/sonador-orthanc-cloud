'''	Orthanc views providing DICOMweb compatability
'''
import posixpath, pydicom, logging, json, copy, datetime
import orthanc

from sqlalchemy.orm import joinedload

from client.errors import ConfigurationError
from client.utils.object import omit, pick

from sonador.apisettings import \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES,  \
	DCMHEADER_MODALITIES_IN_STUDY, DCMHEADER_MODALITY, DCMHEADER_STUDY_DATE
from sonador.imaging.helpers.conversion import json2dcmjson
from sonador.serialization import dcm_str2date

from ..apisettings import ORTHANC_CONFIG_SECTION_DICOMWEB, ORTHANC_CONFIG_SECTION_POSTGRES, \
	ORTHANC_CONFIG_SECTION_EXTRADICOMTAGS, ORTHANC_MAINDICOM_TAGS_DEFAULT
from ..helpers import orthanc_maindicom_tags
from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.helpers import dcmquery2psqlregex

from .base import OrthancBaseView
from .study import CacheStudyListBaseView

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


def init_cached_studylist_endpoint_callback(orthanc_conf, OrthancSession):
	'''	Initialize DICOMweb study list endpoint callback
	'''
	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})
	dicomweb_root = dicomweb_conf.get('Root')
	if not dicomweb_root:
		raise ConfigurationError('Unable to initialize Study list endpoint, invalid DICOMweb configuration. '
			+ 'No DICOMweb root defined in configuration.')

	orthanc.LogWarning(('Enabling cached DICOMweb study list at endpoint "%s". Only resources indexed in cache '
			'will be included in searches and data in ExtraMainDicomTags will be incorporated in responses.')
		% posixpath.join(dicomweb_root, 'studies'))

	# Initialize view class instance
	return CacheStudyDicomWebListView.as_view(
		sessionmaker=OrthancSession, cache_dicomtags=orthanc_maindicom_tags(orthanc_conf))
