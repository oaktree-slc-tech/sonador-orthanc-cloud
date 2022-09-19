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
from ..db.helpers import cache_orthanc_seriesjson
from ..dcmquery import CacheSeriesQueryMixin

from .queryview import DicomQueryBaseView

logger = logging.getLogger(__name__)


class CacheSeriesListBaseView(CacheSeriesQueryMixin, DicomQueryBaseView):
	'''	REST series list endpoint which is able to use the Sonador database cache to 
		search for series instances.
	'''
	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)
		self._init_seriesquery(output, uri, request, *args, **kwargs)


class CacheSeriesQueryView(CacheSeriesListBaseView):
	'''	REST API endpoint which is able to use the Sonador cache to look for study instances.
		Implements an interface similar to Orthanc's "/tools/find" endpont.
	'''
	resource_type = IMAGING_SERVER_RESOURCE_SERIES

	def setup(self, output, uri, request, *args, **kwargs):
		request = request or {}
		self.POST = json.loads(request.get('body')) if request.get('body') else {}

		# Retrieve query from request body, omit study/series date so thtey can be applied
		# using a date/time filter.
		self.query = self.POST.get('Query', {})
		self.dicom_query = omit(self.query, 
			(DCMHEADER_STUDY_DATE, DCMHEADER_SERIES_DATE, DCMHEADER_MODALITIES_IN_STUDY))

		super().setup(output, uri, request)

		# Retrieve request components: limit, offset, date filters, and general query parameters.
		self.limit = int(self.POST.get('Limit')) if self.POST.get('Limit') is not None else None
		self.offset = int(self.POST.get('Since')) if self.POST.get('Since') is not None else None
		self.study_modalities = self.query.get(DCMHEADER_MODALITIES_IN_STUDY)
		self.study_date_filter = self.query.get(DCMHEADER_STUDY_DATE)
		self.series_date_filter = self.query.get(DCMHEADER_SERIES_DATE)

	def orthanc_seriesjson(self, cseries):
		'''	Create Orthanc JSON response for the provided cached series
		'''
		return cache_orthanc_seriesjson(cseries, resource_type=self.resource_type)

	def post(self, output, uri, request):
		'''	Return list of series which match the request parameters
		'''
		with self.sessionmaker() as session:

			# Retrieve Orthanc series
			orthanc_series = self.get_serieslist(session)

			# Serialize results to JSON
			return self.send_response(json.dumps(
				[self.orthanc_seriesjson(cs) for cs in self.paginate_query_results(
					orthanc_series, self.offset or 0, self.limit)],
				cls=SonadorJsonEncoder))