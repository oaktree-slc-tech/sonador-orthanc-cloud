import posixpath, pydicom, logging, json, copy, datetime, traceback
import orthanc

from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_

import client.apisettings as gcapicodes
from client.errors import ConfigurationError
from client.utils.object import omit, pick
from client.utils.urls import build_url

from sonador.apisettings import \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES, \
	DCMHEADER_PATIENT_BIRTHDATE, DCMHEADER_MODALITY, DCMHEADER_STUDY_DATE, DCMHEADER_SERIES_DATE, DCMHEADER_SERIES_TIME, \
	DCMHEADER_MODALITIES_IN_STUDY
from sonador.imaging.helpers.conversion import json2dcmjson
from sonador.serialization import dcm_str2date, SonadorJsonEncoder

from sonador_orthanc_common.servers import ResponseLikeObject, local_orthanc_apiurl

from ..apisettings import SONADOR_CACHE_ORDER_BY
from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.helpers import cache_orthanc_patientjson
from ..dcmquery.patient import CachePatientQueryMixin

from .queryview import DicomQueryBaseView
from .resource import SonadorResourceBaseView

logger = logging.getLogger(__name__)


class CachePatientListBaseView(CachePatientQueryMixin, DicomQueryBaseView):
	'''	REST patient list endpoint which is able to use the Sonador database cache to search
		for patient instances.
	'''
	resource_model = CachePatient
	series_date_filter = None
	study_date_filter = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)
		self._init_patientquery(output, uri, request, *args, **kwargs)


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
			(DCMHEADER_PATIENT_BIRTHDATE, DCMHEADER_STUDY_DATE, DCMHEADER_SERIES_DATE, DCMHEADER_MODALITIES_IN_STUDY))

		super().setup(output, uri, request)

		# Retrieve request components: limit, offset, date filters, and general query parameters.
		self.limit = int(self.POST.get('Limit')) if self.POST.get('Limit') is not None else None
		self.offset = int(self.POST.get('Since')) if self.POST.get('Since') is not None else None
		self.study_modalities = self.POST.get(DCMHEADER_MODALITIES_IN_STUDY)
		self.patient_dob_filter = self.query.get(DCMHEADER_PATIENT_BIRTHDATE)	
		self.study_date_filter = self.query.get(DCMHEADER_STUDY_DATE)
		self.series_date_filter = self.query.get(DCMHEADER_SERIES_DATE)
		self.order_by = self.POST.get(SONADOR_CACHE_ORDER_BY)

	def orthanc_patientjson(self, cpatient):
		'''	Create Orthanc JSON response for the provided cached patient
		'''
		return cache_orthanc_patientjson(cpatient, resource_type=self.resource_type)

	def post(self, output, uri, request):
		'''	Return list of patients which match the request parameters
		'''
		try:
			with self.sessionmaker() as session:

				# Retrieve Orthanc patients
				orthanc_patients = self.get_patientlist(session)

				# Serialize results to JSON
				return self.send_response(json.dumps(
					[self.orthanc_patientjson(cp) for cp in self.paginate_query_results(
						orthanc_patients, self.offset or 0, self.limit)],
					cls=SonadorJsonEncoder))

		except ValueError as err:
			logger.error(
				'Unable to execute patient search due to an error. Error: "%s"\n%s' % (err, traceback.format_exc()))
			
			return self.send_response(json.dumps({
				gcapicodes.ERROR: '%s' % err, gcapicodes.STATUS: gcapicodes.FAIL,
			}), status_code=400)

		except Exception as err:
			emsg = 'Unable to exceute patient search due to an error. Error: "%s"'
			logger.error('%s\n%s' % (emsg, traceback.format_exc()))

			return self.send_response(json.dumps({
				gcapicodes.ERROR: emsg, gcapicodes.STATUS: gcapicodes.FAIL
			}), status_code=500)


class SonadorPatientResourceView(SonadorResourceBaseView):
	'''	Orthanc resource view for managing patient data
	'''
	resource_base = 'patients'
	resource_cachemodel = CachePatient
