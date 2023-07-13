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

		# Study query condition
		study_tagquery = self._study_querycondition_or(
			dict((stag, allfields_queryfilter) for stag in self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_STUDY)), **kwargs)
		study_querycondition = CachePatient.studies_collection.any(study_tagquery)

		# Series query condition
		series_tagquery = self._series_querycondition_or(
			dict((sxtag, allfields_queryfilter) for sxtag in self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_SERIES)), **kwargs)
		series_querycondition = CachePatient.studies_collection.any(CacheStudy.series_collection.any(series_tagquery))

		return dcm_resources.filter(patient_tagquery | study_querycondition | series_querycondition)

	def apply_patient_queryfilter(self, dcm_resources, patient_tagname, patient_queryfilter, **kwargs):
		'''	Apply a patient filter to the resource list. For a ptient query, the patient tags are applied
			to the orthanc JSONB property of CachePatient using a regular expressions match.
		'''
		return dcm_resources.filter(self._patient_querycondition(patient_tagname, patient_queryfilter, **kwargs))

	def apply_study_queryfilter(self, dcm_resources, study_tagname, study_queryfilter, **kwargs):
		'''	Apply a study filter to the resource list. For a study query, the tags are applied to the
			orthanc JSONB property of CachePatient.studies_collection relationship using a regular expression match.
		'''
		return dcm_resources.filter(CachePatient.studies_collection.any(
			self._study_querycondition(study_tagname, study_queryfilter, **kwargs)))

	def apply_series_queryfilter(self, dcm_resources, series_tagname, series_queryfilter, **kwargs):
		'''	Apply a series filter to the resource list. For a series query, the tags are applied to the orthanc
			JSONB property of the CachePatient.studies_collection.series_collection relationship using 
			a regular expression match.
		'''
		return dcm_resources.filter(CachePatient.studies_collection.any(
			CacheStudy.series_collection.any(
				self._series_querycondition(series_tagname, series_queryfilter, **kwargs))))

	def get_patientlist(self, session, *args, **kwargs):
		'''	Execute DICOM query and retrieve patient list
		'''
		# Filter results from cache
		patientlist = self.execute_resource_query(session, *args, **kwargs)

		# StudyDate: Look for patients which have studies that fall within a specific range
		# TODO: Add support for parsiing study time values and incorporating those into the request structure.
		if self.study_date_filter:
			sdate_start_ts, sdate_stop_ts = self.parse_dcmdate_queryfilter(self.study_date_filter)

			# Apply filters
			if sdate_start_ts:
				patientlist = patientlist.filter(CachePatient.studies_collection.any(CacheStudy.ts >= sdate_stop_ts))
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
