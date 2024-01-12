import logging

from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_

from client.errors import ConfigurationError
from client.utils.object import pick, omit

from sonador.apisettings import \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES, \
	DCMHEADER_MODALITY, DCMHEADER_STUDY_DATE, DCMHEADER_SERIES_DATE, DCMHEADER_SERIES_TIME, \
	DCMHEADER_MODALITIES_IN_STUDY

from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.helpers import dcmquery_psqlregex_flags

logger = logging.getLogger(__name__)


class CachePatientQueryMixin(object):
	'''	Helper mixin which provides methods for resource queries against the Sonador "Patient" cache.
	'''
	resource_model = CachePatient
	patient_dob_filter = None
	series_date_filter = None
	study_date_filter = None

	def _init_patientquery(self, *args, **kwargs):
		'''	Ensure that required properties and methos for the query object are present
		'''
		if not hasattr(self, 'series_date_filter'):
			raise ConfigurationError(
				'Unable to initialize, `series_date_filter` is a required property for the %s view.' % type(self).__name__)

		if not hasattr(self, 'study_date_filter'):
			raise ConfigurationError(
				'Unable to initialize, `study_date_filter` is a required property for the %s view.' % type(self).__name__)

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

		# Patient query condition: standard DICOM tags
		patient_tagquery = self._patient_querycondition_or(
			dict((ptag, allfields_queryfilter) for ptag in self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_PATIENT)
				if not ptag in dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT, [])),
			**kwargs)
		
		if dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT):

			# Query patient private tags
			patient_private_tagquery = self._patient_querycondition_or(
				dict((ptag, allfields_queryfilter) for ptag in dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT)),
				privatetags=True, **kwargs)
			patient_tagquery |= CachePatient.privatetags.has(patient_private_tagquery)

		# Study query condition: standard DICOM tags
		study_tagquery = self._study_querycondition_or(
			dict((stag, allfields_queryfilter) for stag in self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_STUDY)
				if not stag in dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY, [])),
			**kwargs)
		study_querycondition = CachePatient.studies_collection.any(study_tagquery)

		if dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY):			

			# Query study private tags
			study_private_tagquery = self._study_querycondition_or(
				dict((stag, allfields_queryfilter) for stag in dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY)),
				privatetags=True, **kwargs)
			study_querycondition |= CachePatient.studies_collection.any(CacheStudy.privatetags.has(study_private_tagquery))

		# Series query condition: standard DICOM tags
		series_tagquery = self._series_querycondition_or(
			dict((sxtag, allfields_queryfilter) for sxtag in self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_SERIES)
				if not sxtag in dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES, [])), 
			**kwargs)
		series_querycondition = CachePatient.studies_collection.any(CacheStudy.series_collection.any(series_tagquery))

		if dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES):

			# Query series private tags
			series_private_tagquery = self._series_querycondition_or(
				dict((sxtag, allfields_queryfilter) for sxtag in dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES)),
				privatetags=True, **kwargs)
			series_querycondition |= CachePatient.studies_collection.any(CacheStudy.series_collection.any(
				CacheSeries.privatetags.has(series_private_tagquery)))

		return dcm_resources.filter(patient_tagquery | study_querycondition | series_querycondition)

	def apply_patient_queryfilter(self, dcm_resources, patient_tagname, patient_queryfilter, **kwargs):
		'''	Apply a patient filter to the resource list. For a ptient query, the patient tags are applied
			to the orthanc JSONB property of CachePatient using a regular expressions match.
		'''		
		# Query date/time tags
		if self.dcm_datetags and patient_tagname in self.dcm_datetags.get('Tags', {}) \
			and self.dcm_datetags.get('Tags', {}).get(patient_tagname).resource == IMAGING_SERVER_RESOURCE_PATIENT:

			# Parse query filter to start/stop timestamps
			pdate_dcmts = self.dcm_datetags.get('Tags', {}).get(patient_tagname)
			pdate_start_ts, pdate_stop_ts = self.parse_dcmdate_queryfilter(patient_queryfilter)

			# Apply date filter
			if pdate_start_ts:				
				dcm_resources = dcm_resources.filter(CachePatient.timestamp_tags.any(and_(
					CachePatient.datetime_resource_model.date_tag == pdate_dcmts.date_tag,
					CachePatient.datetime_resource_model.time_tag == pdate_dcmts.time_tag,
					CachePatient.datetime_resource_model.ts >= pdate_start_ts,
				)))

			if pdate_stop_ts:				
				dcm_resources = dcm_resources.filter(CachePatient.timestamp_tags.any(and_(
					CachePatient.datetime_resource_model.date_tag == pdate_dcmts.date_tag,
					CachePatient.datetime_resource_model.time_tag == pdate_dcmts.time_tag,
					CachePatient.datetime_resource_model.ts <= pdate_stop_ts,
				)))
			
			return dcm_resources

		# Query patient private tags
		elif self.dcm_privatetags and self.dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT) \
			and patient_tagname in self.dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT, []):
			return dcm_resources.filter(CachePatient.privatetags.has(
				self._patient_querycondition(patient_tagname, patient_queryfilter, pivatetags=True, **kwargs)))

		# Query primary DICOM attirbutes
		return dcm_resources.filter(self._patient_querycondition(patient_tagname, patient_queryfilter, **kwargs))

	def apply_study_queryfilter(self, dcm_resources, study_tagname, study_queryfilter, **kwargs):
		'''	Apply a study filter to the resource list. For a study query, the tags are applied to the
			orthanc JSONB property of CachePatient.studies_collection relationship using a regular expression match.
		'''	
		# Query date/time tags
		if self.dcm_datetags and study_tagname in self.dcm_datetags.get('Tags', {}) \
			and self.dcm_datetags.get('Tags', {}).get(study_tagname).resource == IMAGING_SERVER_RESOURCE_STUDY:

			# Parse query filter to start/stop timestamps
			sdate_dcmts = self.dcm_datetags.get('Tags', {}).get(study_tagname)
			sdate_start_ts, sdate_stop_ts = self.parse_dcmdate_queryfilter(study_queryfilter)

			# Apply date filter
			if sdate_start_ts:				
				dcm_resources = dcm_resources.filter(CachePatient.studies_collection.any(
					CacheStudy.timestamp_tags.any(and_(
						CacheStudy.datetime_resource_model.date_tag == sdate_dcmts.date_tag,
						CacheStudy.datetime_resource_model.time_tag == sdate_dcmts.time_tag,
						CacheStudy.datetime_resource_model.ts >= sdate_start_ts,
				))))

			if sdate_stop_ts:				
				dcm_resources = dcm_resources.filter(CachePatient.studies_collection.any(
					CacheStudy.timestamp_tags.any(and_(
						CacheStudy.datetime_resource_model.date_tag == sdate_dcmts.date_tag,
						CacheStudy.datetime_resource_model.time_tag == sdate_dcmts.time_tag,
						CacheStudy.datetime_resource_model.ts <= sdate_stop_ts,
				))))
			
			return dcm_resources

		# Query study private tags
		elif self.dcm_privatetags and self.dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY) \
			and study_tagname in self.dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY, []):			
			return dcm_resources.filter(CachePatient.studies_collection.any(
				CacheStudy.privatetags.has(self._study_querycondition(study_tagname, study_queryfilter, privatetags=True, **kwargs))))

		# Query primary DICOM attributes
		return dcm_resources.filter(CachePatient.studies_collection.any(
			self._study_querycondition(study_tagname, study_queryfilter, **kwargs)))

	def apply_series_queryfilter(self, dcm_resources, series_tagname, series_queryfilter, **kwargs):
		'''	Apply a series filter to the resource list. For a series query, the tags are applied to the orthanc
			JSONB property of the CachePatient.studies_collection.series_collection relationship using 
			a regular expression match.
		'''
		# Query date/time tags
		if self.dcm_datetags and series_tagname in self.dcm_datetags.get('Tags', {}) \
			and self.dcm_datetags.get('Tags', {}).get(series_tagname).resource == IMAGING_SERVER_RESOURCE_SERIES:

			# Parse query filter to start/stop timestamps
			sxdate_dcmts = self.dcm_datetags.get('Tags', {}).get(series_tagname)
			sxdate_start_ts, sxdate_stop_ts = self.parse_dcmdate_queryfilter(series_queryfilter)

			# Apply date filter
			if sxdate_start_ts:				
				dcm_resources = dcm_resources.filter(CachePatient.studies_collection.any(
					CacheStudy.series_collection.any(CacheSeries.timestamp_tags.any(and_(
						CacheSeries.datetime_resource_model.date_tag == sxdate_dcmts.date_tag,
						CacheSeries.datetime_resource_model.time_tag == sxdate_dcmts.time_tag,
						CacheSeries.datetime_resource_model.ts >= sxdate_start_ts,
				)))))

			if sxdate_stop_ts:				
				dcm_resources = dcm_resources.filter(CachePatient.studies_collection.any(
					CacheStudy.series_collection.any(CacheSeries.timestamp_tags.any(and_(
						CacheSeries.datetime_resource_model.date_tag == sxdate_dcmts.date_tag,
						CacheSeries.datetime_resource_model.time_tag == sxdate_dcmts.time_tag,
						CacheSeries.datetime_resource_model.ts <= sxdate_stop_ts,
				)))))
			
			return dcm_resources

		# Query series private tags
		elif self.dcm_privatetags and self.dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES) \
			and series_tagname in self.dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES, []):
			return dcm_resources.filter(CachePatient.studies_collection.any(
				CacheStudy.series_collection.any(
					CacheSeries.privatetags.has(
						self._series_querycondition(series_tagname, series_queryfilter, privatetags=True, **kwargs)))))

		# Query primary DICOM attributes
		return dcm_resources.filter(CachePatient.studies_collection.any(
			CacheStudy.series_collection.any(
				self._series_querycondition(series_tagname, series_queryfilter, **kwargs))))

	def get_patientlist(self, session, *args, **kwargs):
		'''	Execute DICOM query and retrieve patient list
		'''
		# Filter results from cache
		patientlist = self.execute_resource_query(session, *args, **kwargs)

		# Patient DOB
		if self.patient_dob_filter:
			pdob_start_ts, pdob_stop_ts = self.parse_dcmdate_queryfilter(self.patient_dob_filter)

			# Apply filters
			if pdob_start_ts:
				patientlist = patientlist.filter(CachePatient.birth_date >= pdob_start_ts)
			if pdob_stop_ts:
				patientlist = patientlist.filter(CachePatient.birth_date <= pdob_stop_ts)

		# StudyDate: Look for patients which have studies that fall within a specific range
		# TODO: Add support for parsiing study time values and incorporating those into the request structure.
		if self.study_date_filter:
			sdate_start_ts, sdate_stop_ts = self.parse_dcmdate_queryfilter(self.study_date_filter)

			# Apply filters
			if sdate_start_ts:
				patientlist = patientlist.filter(CachePatient.studies_collection.any(CacheStudy.ts >= sdate_start_ts))
			if sdate_stop_ts:
				patientlist = patientlist.filter(CachePatient.studies_collection.any(CacheStudy.ts <= sdate_stop_ts))

		# SeriesDate: Look for patients with series that fall within a specific range
		if self.series_date_filter:
			sxdate_start_ts, sxdate_stop_ts = self.parse_dcmdate_queryfilter(self.series_date_filter)

			# Apply filters
			if sxdate_start_ts:
				patientlist = patientlist.filter(CachePatient.studies_collection.any(
					CacheStudy.series_collection.any(CacheSeries.ts >= sxdate_start_ts)))
			if sxdate_stop_ts:
				patientlist = patientlist.filter(CachePatient.studies_collection.any(
					CacheStudy.series_collection.any(CacheSeries.ts <= sxdate_stop_ts)))

		return patientlist
