import logging

from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_

from client.errors import ConfigurationError
from client.utils.object import omit, pick

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES, \
	DCM_QUERY_ALLFIELDS, DCMHEADER_MODALITIES_IN_STUDY, DCMHEADER_SERIES_DATE, DCMHEADER_STUDY_DATE, \
	DCMHEADER_MODALITY

from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.helpers import dcmquery_psqlregex_flags

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

	def apply_allfields_queryfilter(self, dcm_resources, allfields_queryfilter, **kwargs):
		'''	Apply an "all fields" filter to the DICOM resource list

			@input dcm_resources (sqlalchemy.orm.query.Query): resource query to which the
				all fields query filter should be applied.
			@input allfields_queryfilter (str): query filter to apply

			@returns filtered query
		'''
		# Patient query condition
		patient_tagquery = self._patient_querycondition_or(
			dict((ptag, allfields_queryfilter) for ptag in self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_PATIENT)), **kwargs)
		patient_querycondition = CacheSeries.parent.has(CacheStudy.parent.has(patient_tagquery))

		# Study query condition
		study_tagquery = self._study_querycondition_or(
			dict((stag, allfields_queryfilter) for stag in self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_STUDY)), **kwargs)
		study_querycondition = CacheSeries.parent.has(study_tagquery)

		# Series query condition
		series_tagquery = self._series_querycondition_or(
			dict((sxtag, allfields_queryfilter) for sxtag in self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_SERIES)), **kwargs)

		return dcm_resources.filter(patient_querycondition | study_querycondition | series_tagquery)

	def apply_patient_queryfilter(self, dcm_resources, patient_tagname, patient_queryfilter, **kwargs):
		'''	Apply a patient filter to the resource list. For a series query, the patient tags are applied
			to the orthanc JSONB property of the CacheSeries.parent.parent relationships using a regular
			expression match.
		'''
		return dcm_resources.filter(CacheSeries.parent.has(CacheStudy.parent.has(
			self._patient_querycondition(patient_tagname, patient_queryfilter, **kwargs))))

	def apply_study_queryfilter(self, dcm_resources, study_tagname, study_queryfilter, **kwargs):
		''' Apply a study filter to the resource list. For a study query, the tags are applied to the
			orthanc JSONB property of CacheSeries.parent using a regular expression match.
		'''
		return dcm_resources.filter(CacheSeries.parent.has((
			self._study_querycondition(study_tagname, study_queryfilter, **kwargs))))

	def apply_series_queryfilter(self, dcm_resources, series_tagname, series_queryfilter, **kwargs):
		'''	Apply a series filter to the resource list. For a series query, the tags are applied to the
			CacheSeries.orthanc JSON property using a regular expressions match.
		'''
		return dcm_resources.filter(self._series_querycondition(series_tagname, series_queryfilter, **kwargs))

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