import logging

from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_

from client.errors import ConfigurationError
from client.utils.object import omit, pick

from sonador.apisettings import DCMHEADER_MODALITIES_IN_STUDY, DCMHEADER_SERIES_DATE, DCMHEADER_STUDY_DATE

from ..db.cache import CachePatient, CacheStudy, CacheSeries

logger = logging.getLogger(__name__)


class CacheSeriesQueryMixin(object):
	'''	Helper mixin which provides methods for resource queries against the Sonador "Series" cache.
	'''
	resource_model = CacheSeries
	series_date_filter = None
	study_date_filter = None
	study_modalities = None

	def _init_seriesquery(self, *args, **kwargs):
		'''	Ensure that required properties and methods for the query object are present
		'''
		if not hasattr(self, 'series_date_filter'):
			raise ConfigurationError(
				'Unable to initialize, `series_date_filter` is a required property for the %s query class.' % type(self).__name__)

		if not hasattr(self, 'study_date_filter'):
			raise ConfigurationError(
				'Unable to initialize, `study_date_filter` is a required property for the %s query class.' % type(self).__name__)

	def apply_patient_queryfilter(self, dcm_resources, patient_tagname, patient_queryfilter, *args, **kwargs):
		'''	Apply a patient filter to the resource list. For a series query, the patient tags are applied
			to the orthanc JSONB property of the CacheSeries.parent.parent relationships using a regular
			expression match.
		'''
		return dcm_resources.filter(CacheSeries.parent.has(
			CacheStudy.parent.has(
				CachePatient.orthanc[patient_tagname].astext.regexp_match(patient_queryfilter))))

	def apply_study_queryfilter(self, dcm_resources, study_tagname, study_queryfilter, *args, **kwargs):
		''' Apply a study filter to the resource list. For a study query, the tags are applied to the
			orthanc JSONB property of CacheSeries.parent using a regular expression match.
		'''
		return dcm_resources.filter(CacheSeries.parent.has(
			CacheStudy.orthanc[study_tagname].astext.regexp_match(study_queryfilter)))

	def apply_series_queryfilter(self, dcm_resources, series_tagname, series_queryfilter, *args, **kwargs):
		'''	Apply a series filter to the resource list. For a series query, the tags are applied to the
			CacheSeries.orthanc JSON property using a regular expressions match.
		'''
		return dcm_resources.filter(
			CacheSeries.orthanc[series_tagname].astext.regexp_match(series_queryfilter))

	def get_serieslist(self, session, *args, **kwargs):
		''' Execute DICOM query and retrieve series list
		'''
		# Filter results from cache
		serieslist = self.execute_resource_query(session, *args, **kwargs)

		# Check if a specific modality is contained in the parent study
		if self.study_modalities:

			# Apply modality list to filter (OR query)
			serieslist = serieslist.filter(
				CacheSeries.parent.has(
					CacheStudy.series_collection.any(or_(*tuple(map(
						lambda  modality: CacheSeries.orthanc[DCMHEADER_MODALITY].astext == modality,
						self.parse_multivalue_queryfilter(self.study_modalities)))))))

		# SeriesDate: Check if a specific date range is requested.
		# TODO: Add support for parsing series time values and incorporating those into the request structure.
		if self.series_date_filter:
			sxdate_start_ts, sxdate_stop_ts = self.parse_dcmdate_queryfilter(self.series_date_filter)

			# Apply series date filters
			if sxdate_start_ts:
				serieslist = serieslist.filter(CacheSeries.ts >= sxdate_start_ts)
			if sxdate_stop_ts:
				serieslist = serieslist.filter(CacheSeries.ts <= sxdate_stop_ts)

		# StudyDate: check if a spcecifif date range is requested
		if self.study_date_filter:
			sdate_start_ts, sdate_stop_ts = self.parse_dcmdate_queryfilter(self.study_date_filter)

			# Apply study date filters
			if sdate_start_ts:
				serieslist = serieslist.filter(CacheSeries.parent.has(CacheStudy.ts >= sdate_start_ts))
			if sdate_stop_ts:
				serieslist = serieslist.filter(CacheSeries.parent.has(CacheStudy.ts <= sdate_stop_ts))

		return serieslist