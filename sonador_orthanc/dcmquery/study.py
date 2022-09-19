import logging

from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_

from client.errors import ConfigurationError
from client.utils.object import omit, pick

from sonador.apisettings import DCMHEADER_MODALITIES_IN_STUDY, DCMHEADER_MODALITY, \
	DCMHEADER_SERIES_DATE, DCMHEADER_STUDY_DATE

from ..db.cache import CachePatient, CacheStudy, CacheSeries

logger = logging.getLogger(__name__)


class CacheStudyQueryMixin(object):
	'''	Helper mixin which provides methods for resource queries against the Sonador "Study" cache.
	'''
	resource_model = CacheStudy
	study_date_filter = None
	series_date_filter = None
	study_modalities = None

	def _init_studyquery(self, *args, **kwargs):
		'''	Ensure that the required properties and methods for the query object are present.
		'''
		if not hasattr(self, 'study_date_filter'):
			raise ConfigurationError(
				'Unable to initialize, `study_date_filter` is a required property for the %s view' % type(self).__name__)

		if not hasattr(self, 'series_date_filter'):
			raise ConfigurationError(
				'Unable to initialize, `series_date_filter` is a required property for the %s view' % type(self).__name__)

	def apply_patient_queryfilter(self, dcm_resources, patient_tagname, patient_queryfilter, *args, **kwargs):
		'''	Apply a patient filter to the resource list. For a study query the patient tags are applied
			to the orthanc JSONB property of the CacheStudy.parent relationship using a regular expression match.
		'''
		return dcm_resources.filter(CacheStudy.parent.has(
			CachePatient.orthanc[patient_tagname].astext.regexp_match(patient_queryfilter)))

	def apply_study_queryfilter(self, dcm_resources, study_tagname, study_queryfilter, *args, **kwargs):
		'''	Apply a study filter to the resource list. For a study query, the tags are applied to the 
			CacheStudy.orthanc JSONB property using a regular expression match.
		'''
		return dcm_resources.filter(
			CacheStudy.orthanc[study_tagname].astext.regexp_match(study_queryfilter))

	def apply_series_queryfilter(self, dcm_resources, series_tagname, series_queryfilter, *args, **kwargs):
		'''	Apply a series filter to the resource list. For a series query, the tags are applied to the 
			CacheStudy.series_collection property.
		'''
		return dcm_resources.filter(
			CacheStudy.series_collection.any(
				CacheSeries.orthanc[series_tagname].astext.regexp_match(series_queryfilter)))

	def get_studylist(self, session, *args, **kwargs):
		'''	Execute DICOM query and retrieve study list
		'''
		# Filter results from cache
		studylist = self.execute_resource_query(session, *args, **kwargs)

		# Check if a specific modality is contained in the study
		if self.study_modalities:

			# Apply modality list to filter (OR query)
			studylist = studylist.filter(
				CacheStudy.series_collection.any(or_(*tuple(map(
					lambda  modality: CacheSeries.orthanc[DCMHEADER_MODALITY].astext == modality,
					self.parse_multivalue_queryfilter(self.study_modalities))))))

		# StudyDate: Check if a specific date range is requested.
		# TODO: Add support for parsing study time values and incorporating those into the request structure.
		if self.study_date_filter:
			sdate_start_ts, sdate_stop_ts = self.parse_dcmdate_queryfilter(self.study_date_filter)

			# Apply filters
			if sdate_start_ts:
				studylist = studylist.filter(CacheStudy.ts >= sdate_start_ts)
			if sdate_stop_ts:
				studylist = studylist.filter(CacheStudy.ts <= sdate_stop_ts)

		# SeriesDate: Check if a specific date range is requested in series which belong to the study
		if self.series_date_filter:
			sxdate_start_ts, sxdate_stop_ts = self.parse_dcmdate_queryfilter(self.series_date_filter)

			# Apply filters
			if sxdate_start_ts:
				studylist = studylist.filter(CacheStudy.series_collection.any(CacheSeries.ts >= sxdate_start_ts))
			if sxdate_stop_ts:
				studylist = studylist.filter(CacheStudy.series_collection.any(CacheSeries.ts <= sxdate_stop_ts))

		return studylist
