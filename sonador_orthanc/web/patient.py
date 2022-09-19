import posixpath, pydicom, logging, json, copy, datetime
import orthanc

from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_

from client.errors import ConfigurationError
from client.utils.object import omit, pick

from sonador.apisettings import \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES, \
	DCMHEADER_MODALITY, DCMHEADER_STUDY_DATE, DCMHEADER_SERIES_DATE, DCMHEADER_SERIES_TIME, \
	DCMHEADER_MODALITIES_IN_STUDY
from sonador.imaging.helpers.conversion import json2dcmjson
from sonador.serialization import dcm_str2date, SonadorJsonEncoder

from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.helpers import cache_orthanc_patientjson

from .queryview import DicomQueryBaseView

logger = logging.getLogger(__name__)


class CachePatientListBaseView(DicomQueryBaseView):
	'''	REST patient list endpoint which is able to use the Sonador database cache to search
		for patient instances.
	'''
	resource_model = CachePatient
	series_date_filter = None
	study_date_filter = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		if not hasattr(self, 'series_date_filter'):
			raise ConfigurationError(
				'Unable to initialize, `series_date_filter` is a required property for the %s view.' % type(self).__name__)

		if not hasattr(self, 'study_date_filter'):
			raise ConfigurationError(
				'Unable to initialize, `study_date_filter` is a required property for the %s view.' % type(self).__name__)

	def apply_patient_queryfilter(self, dcm_resources, patient_tagname, patient_queryfilter, *args, **kwargs):
		'''	Apply a patient filter to the resource list. For a ptient query, the patient tags are applied
			to the orthanc JSONB property of CachePatient using a regular expressions match.
		'''
		return dcm_resources.filter(CachePatient.orthanc[patient_tagname].astext.regexp_match(patient_queryfilter))

	def apply_study_queryfilter(self, dcm_resources, study_tagname, study_queryfilter, *args, **kwargs):
		'''	Apply a study filter to the resource list. For a study query, the tags are applied to the
			orthanc JSONB property of CachePatient.studies_collection relationship using a regular expression match.
		'''
		return dcm_resources.filter(CachePatient.studies_collection.any(
			CacheStudy.orthanc[study_tagname].astext.regexp_match(study_queryfilter)))

	def apply_series_queryfilter(self, dcm_resources, series_tagname, series_queryfilter, *args, **kwargs):
		'''	Apply a series filter to the resource list. For a series query, the tags are applied to the orthanc
			JSONB property of the CachePatient.studies_collection.series_collection relationship using 
			a regular expression match.
		'''
		return dcm_resources.filter(CachePatient.studies_collection.any(
				CacheStudy.series_collection.any(
					CacheSeries.orthanc[series_tagname].astext.regexp_match(series_queryfilter))))

	def get_patientlist(self, session, *args, **kwargs):
		'''	Execute DICOM query and retrieve patient list
		'''
		# Filter results from cache
		patientlist = self.execute_resource_query(session, *args, **kwargs)

		return patientlist


class CachePatientQueryView(CachePatientListBaseView):
	'''	REST API endpoint which is able to use the Sonador cache to search for patient instances.
		Implements an interface similar to Orthanc's "/tools/find" endpoint.
	'''	
	resource_type = IMAGING_SERVER_RESOURCE_PATIENT

	def setup(self, output, uri, request, *args, **kwargs):
		request = request or {}
		self.POST = json.loads(request.get('body')) if request.get('body') else {}

		# Retrieve query from request body, omit study and series date so that they can be 
		# applied using a date/time filter.
		self.query = self.POST.get('Query', {})
		self.dicom_query = omit(self.query, 
			(DCMHEADER_STUDY_DATE, DCMHEADER_SERIES_DATE, DCMHEADER_MODALITIES_IN_STUDY))

		super().setup(output, uri, request)

		# Retrieve request components: limit, offset, date filters, and general query parameters.
		self.limit = int(self.POST.get('Limit')) if self.POST.get('Limit') is not None else None
		self.offset = int(self.POST.get('Since')) if self.POST.get('Since') is not None else None
		self.study_modalities = self.POST.get(DCMHEADER_MODALITIES_IN_STUDY)
		self.study_date_filter = self.query.get(DCMHEADER_STUDY_DATE)
		self.series_date_filter = self.query.get(DCMHEADER_SERIES_DATE)

	def orthanc_patientjson(self, cpatient):
		'''	Create Orthanc JSON response for the provided cached patient
		'''
		return cache_orthanc_patientjson(cpatient, resource_type=self.resource_type)

	def post(self, output, uri, request):
		'''	Return list of patients which match the request parameters
		'''
		with self.sessionmaker() as session:

			# Retrieve Orthanc patients
			orthanc_patients = self.get_patientlist(session)

			# Serialize results to JSON
			return self.send_response(json.dumps(
				[self.orthanc_patientjson(cp) for cp in self.paginate_query_results(
					orthanc_patients, self.offset or 0, self.limit)],
				cls=SonadorJsonEncoder))
