import logging

from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_

from client.errors import ConfigurationError
from client.utils.object import omit, pick

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES, \
	DCMHEADER_MODALITIES_IN_STUDY, DCMHEADER_MODALITY, DCMHEADER_SERIES_DATE, DCMHEADER_STUDY_DATE

from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.helpers import dcmquery_psqlregex_flags

logger = logging.getLogger(__name__)


class CacheStudyQueryMixin(object):
	'''	Helper mixin which provides methods for resource queries against the Sonador "Study" cache.
	'''
	resource_model = CacheStudy
	patient_dob_filter = None
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

	def apply_session_options(self, session, basequery, *args, **kwargs):
		'''	Create join between primary and private DICOM cache tables
		'''
		basequery = super().apply_session_options(session, basequery, *args, **kwargs)
		return basequery.options(joinedload(self.resource_model.privatetags))

	def apply_allfields_queryfilter(self, dcm_resources, allfields_queryfilter, **kwargs):
		'''	Apply an "all fields" filter to the DICOM resource list

			@input dcm_resources (sqlalchemy.orm.query.Query): resource query to which the
				all fields query filter should be applied.
			@input allfields_queryfilter (str): query filter to apply

			@returns filtered query
		'''
		dcm_privatetags = getattr(self, 'dcm_privatetags', None) or {}

		# Patient query condition
		patient_tagquery = self._patient_querycondition_or(
			dict((ptag, allfields_queryfilter) for ptag in self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_PATIENT)
				if not ptag in dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT, [])), 
			**kwargs)
		patient_querycondition = CacheStudy.parent.has(patient_tagquery)

		if dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT):

			# Query patient private tags
			patient_private_tagquery = self._patient_querycondition_or(
				dict((ptag, allfields_queryfilter) for ptag in dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT)),
				privatetags=True, **kwargs)
			patient_querycondition |= CacheStudy.parent.has(CachePatient.privatetags.has(patient_private_tagquery))

		# Study query condition
		study_tagquery = self._study_querycondition_or(
			dict((stag, allfields_queryfilter) for stag in self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_STUDY)
				if not stag in dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY)),
			**kwargs)

		if dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY):
			
			# Query study private tags
			study_private_tagquery = self._study_querycondition_or(
				dict((stag, allfields_queryfilter) for stag in dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY)),
				privatetags=True, **kwargs)
			study_tagquery |= study_private_tagquery

		# Series query condition
		series_tagquery = self._series_querycondition_or(
			dict((sxtag, allfields_queryfilter) for sxtag in self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_SERIES)), **kwargs)
		series_querycondition = CacheStudy.series_collection.any(series_tagquery)

		if dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES):

			# Query series private tags
			series_private_tagquery = self._series_querycondition_or(
				dict((sxtag, allfields_queryfilter) for sxtag in dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES)),
				privatetags=True, **kwargs)
			series_querycondition |= CacheStudy.series_collection.any(CacheSeries.privatetags.has(series_private_tagquery))

		return dcm_resources.filter(patient_querycondition | study_tagquery | series_querycondition)
	
	def apply_patient_queryfilter(self, 
			dcm_resources, patient_tagname, patient_queryfilter, **kwargs):
		'''	Apply a patient filter to the resource list. For a study query the patient tags are applied
			to the orthanc JSONB property of the CacheStudy.parent relationship using a regular expression match.
		'''	
		# Query date/time tags
		if self.dcm_datetags and patient_tagname in self.dcm_datetags.get('Tags', {}) \
			and self.dcm_datetags.get('Tags', {}).get(patient_tagname).resource == IMAGING_SERVER_RESOURCE_PATIENT:

			# Parse query filter to start/stop timestamps
			pdate_dcmts = self.dcm_datetags.get('Tags', {}).get(patient_tagname)
			pdate_start_ts, pdate_stop_ts = self.parse_dcmdate_queryfilter(patient_queryfilter)

			# Apply date filter
			if pdate_start_ts:				
				dcm_resources = dcm_resources.filter(CacheStudy.parent.has(
					CachePatient.timestamp_tags.any(and_(
						CachePatient.datetime_resource_model.date_tag == pdate_dcmts.date_tag,
						CachePatient.datetime_resource_model.time_tag == pdate_dcmts.time_tag,
						CachePatient.datetime_resource_model.ts >= pdate_start_ts,
				))))

			if pdate_stop_ts:				
				dcm_resources = dcm_resources.filter(CacheStudy.parent.has(
					CachePatient.timestamp_tags.any(and_(
						CachePatient.datetime_resource_model.date_tag == pdate_dcmts.date_tag,
						CachePatient.datetime_resource_model.time_tag == pdate_dcmts.time_tag,
						CachePatient.datetime_resource_model.ts <= pdate_stop_ts,
				))))
			
			return dcm_resources

		# Query patient private tags
		if self.dcm_privatetags and self.dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT) \
			and patient_tagname in self.dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT, []):
			return dcm_resources.filter(CacheStudy.parent.has(
				CachePatient.privatetags.has(
					self._patient_querycondition(patient_tagname, patient_queryfilter, privatetags=True, **kwargs))))

		# Query primay DICOM attributes
		return dcm_resources.filter(CacheStudy.parent.has(
			self._patient_querycondition(patient_tagname, patient_queryfilter, **kwargs)))

	def apply_study_queryfilter(self, 
			dcm_resources, study_tagname, study_queryfilter, **kwargs):
		'''	Apply a study filter to the resource list. For a study query, the tags are applied to the 
			CacheStudy.orthanc JSONB property using a regular expression match.
		'''
		# Query date/time tags
		if self.dcm_datetags and study_tagname in self.dcm_datetags.get('Tags', {}) \
			and self.dcm_datetags.get('Tags', {}).get(study_tagname).resource == IMAGING_SERVER_RESOURCE_STUDY:

			# Parse query filter to start/stop timestamps
			sdate_dcmts = self.dcm_datetags.get('Tags', {}).get(study_tagname)
			sdate_start_ts, sdate_stop_ts = self.parse_dcmdate_queryfilter(study_queryfilter)

			# Apply date filter
			if sdate_start_ts:				
				dcm_resources = dcm_resources.filter(CacheStudy.timestamp_tags.any(and_(
					CacheStudy.datetime_resource_model.date_tag == sdate_dcmts.date_tag,
					CacheStudy.datetime_resource_model.time_tag == sdate_dcmts.time_tag,
					CacheStudy.datetime_resource_model.ts >= sdate_start_ts,
				)))

			if sdate_stop_ts:				
				dcm_resources = dcm_resources.filter(CacheStudy.timestamp_tags.any(and_(
					CacheStudy.datetime_resource_model.date_tag == sdate_dcmts.date_tag,
					CacheStudy.datetime_resource_model.time_tag == sdate_dcmts.time_tag,
					CacheStudy.datetime_resource_model.ts <= sdate_stop_ts,
				)))
			
			return dcm_resources

		# Query study private tags
		elif self.dcm_privatetags and self.dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY) \
			and study_tagname in self.dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY, []):
			return dcm_resources.filter(CacheStudy.privatetags.has(
				self._study_querycondition(study_tagname, study_queryfilter, privatetags=True, **kwargs)))

		# Query primary DICOM attributes
		return dcm_resources.filter(self._study_querycondition(study_tagname, study_queryfilter, **kwargs))

	def apply_series_queryfilter(self, 
			dcm_resources, series_tagname, series_queryfilter, **kwargs):
		'''	Apply a series filter to the resource list. For a series query, the tags are applied to the 
			CacheStudy.series_collection property.
		'''
		# Query date/time tags
		if self.dcm_datetags and series_tagname in self.dcm_datetags.get('Tags', {}) \
			and self.dcm_datetags.get('Tags', {}).get(series_tagname).resource == IMAGING_SERVER_RESOURCE_SERIES:

			# Parse query filter to start/stop timestamps
			sxdate_dcmts = self.dcm_datetags.get('Tags', {}).get(series_tagname)
			sxdate_start_ts, sxdate_stop_ts = self.parse_dcmdate_queryfilter(series_queryfilter)

			# Apply date filter
			if sxdate_start_ts:				
				dcm_resources = dcm_resources.filter(CacheStudy.series_collection.any(CacheSeries.timestamp_tags.any(and_(
					CacheSeries.datetime_resource_model.date_tag == sxdate_dcmts.date_tag,
					CacheSeries.datetime_resource_model.time_tag == sxdate_dcmts.time_tag,
					CacheSeries.datetime_resource_model.ts >= sxdate_start_ts,
				))))

			if sxdate_stop_ts:				
				dcm_resources = dcm_resources.filter(CacheStudy.series_collection.any(CacheSeries.timestamp_tags.any(and_(
					CacheSeries.datetime_resource_model.date_tag == sxdate_dcmts.date_tag,
					CacheSeries.datetime_resource_model.time_tag == sxdate_dcmts.time_tag,
					CacheSeries.datetime_resource_model.ts <= sxdate_stop_ts,
				))))
			
			return dcm_resources

		# Query series private tags
		if self.dcm_privatetags and self.dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES) \
			and series_tagname in self.dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES, []):
			return dcm_resources.filter(CacheStudy.series_collection.any(
				CacheSeries.privatetags.has(
					self._series_querycondition(series_tagname, series_queryfilter, privatetags=True, **kwargs))))

		# Query primary DICOM attributes
		return dcm_resources.filter(CacheStudy.series_collection.any(
			self._series_querycondition(series_tagname, series_queryfilter, **kwargs)))

	def get_studylist(self, session, *args, **kwargs):
		'''	Execute DICOM query and retrieve study list
		'''
		# Filter results from cache
		studylist = self.execute_resource_query(session, *args, **kwargs)

		# Patient DOB
		if self.patient_dob_filter:
			pdob_start_ts, pdob_stop_ts = self.parse_dcmdate_queryfilter(self.patient_dob_filter)

			# Apply filters
			if pdob_start_ts:
				studylist = studylist.filter(CacheStudy.parent.has(CachePatient.birth_date >= pdob_start_ts))
			if pdob_stop_ts:
				studylist = studylist.filter(CacheStudy.parent.has(CachePatient.birth_date <= pdob_stop_ts))

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
