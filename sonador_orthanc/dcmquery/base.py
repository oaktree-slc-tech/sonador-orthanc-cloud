import logging, abc, datetime
from abc import ABC
from typing import Union

from client.errors import ConfigurationError
from client.utils.object import omit, pick

from sonador.apisettings import \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES
from sonador.serialization import dcm_str2date

from ..db.helpers import dcmquery2psqlregex

logger = logging.getLogger(__name__)


class DicomQueryMixin(ABC):
	'''	Helper mixin which provides methods for translating DICOM queries into Sonador
		resource cache queries
	'''
	sessionmaker = None
	resource_model = None
	cache_dicomtags = None
	dicom_query = None

	def _init_dcmquery(self, *args, **kwargs):
		'''	Ensure that required class components are present
		'''
		# Ensure sessionmaker instance is available
		if self.sessionmaker is None:
			raise ConfigurationError(
				'Unable to initialize %s instance: invalid session maker instance' % type(self).__name__)

		# Ensure that a resource model has been specified
		if self.resource_model is None:
			raise ConfigurationError(
				'Unable to initialize %s instance: invalid resource model' % type(self).__name__)

		# Ensure that DICOM tags cache is available
		if self.cache_dicomtags is None:
			raise ConfigurationError(
				'Unable to initialize %s instance: invalid DICOM tags cache' % type(self).__name__)

		# Ensure that a DICOM query has been specified
		if self.dicom_query is None:
			raise ConfigurationError(
				'Unable to initialize %s instance: no DICOM query specified' % type(self).__name__)

	def get_base_resourcelist(self, session, *args, **kwargs):
		'''	Retrieve base query for the view
		'''
		return session.query(self.resource_model)

	def apply_session_options(self, session, basequery, *args, **kwargs):
		'''	Apply options to the base query. Provides a hook to load related data or apply modify
			special database options.
		'''
		return basequery

	def dcmquery2psqlregex(self, dcmquery_val, *args, **kwargs):
		'''	Translate the DICOM query value to a PSQL compatible query value
		'''
		return dcmquery2psqlregex(dcmquery_val)

	def parse_dcmdate_queryfilter(self, dcmquery_val: str, range_sep='-'):
		'''	Parse the provide DICOM query value to a start/end time for querying the resource cache
		'''
		rdate_start_ts = rdate_stop_ts = None

		# Parse date range
		if range_sep in dcmquery_val:

			# Parse start and stop dates from request query parameter
			rdate_components = dcmquery_val.split(range_sep)
			if not len(rdate_components) == 2:
				raise ValueError('Unable to parse DICOM date filter. Invalid range: %s'  % dcmquery_val)

			# Parse to dates. For ranges with a start component (but not a stop component),
			# OHIF will a value less than the start. If that is the case, set the stop component to be None.
			rdate_start, rdate_stop = dcm_str2date(rdate_components[0]), dcm_str2date(rdate_components[1])
			if rdate_stop < rdate_start:
				rdate_stop = None

			if rdate_start:
				rdate_start_ts = datetime.datetime.combine(rdate_start, datetime.time(0,0,0))
			if rdate_stop:
				rdate_stop_ts = datetime.datetime.combine(rdate_stop, datetime.time(23,59,59))

			# Convert start/stop components to datetime and filter against timestamp of studies.
			# Filter is inclusive (midnight AM on start to midnight PM on stop).
			if rdate_start:
				rdate_start_ts = datetime.datetime.combine(rdate_start, datetime.time(0,0,0))
				logger.debug('StudyDate start: %s' % rdate_start_ts)	

			if rdate_stop:
				rdate_stop_ts = datetime.datetime.combine(rdate_stop, datetime.time(23,59,59))
				logger.debug('StudyDate stop: %s' % rdate_stop_ts)
				
		# Match study date exactly. Because the CacheStudy.ts is a date/time, a range must
		# be used to match studies for the desired time period.
		else:
			rdate = dcm_str2date(self.study_date_filter)
			rdate_start_ts = datetime.datetime.combine(rdate, datetime.time(0,0,0))
			rdate_stop_ts = datetime.datetime.combine(rdate, datetime.time(23,59,59))

		return rdate_start_ts, rdate_stop_ts

	def parse_multivalue_queryfilter(self, dcm_queryval, sep=r'\\', *args, **kwargs):
		''' Split a multi-value query filter into an iterable of conditions
		'''
		queryval = dcm_queryval

		if isinstance(queryval, str):
			queryval = queryval.replace(',', sep).split(sep)

		return queryval

	@abc.abstractmethod
	def apply_patient_queryfilter(self, dcm_resources, patient_tagname, patient_queryfilter, *args, **kwargs):
		'''	Apply a patient filter to the DICOM resource list

			@input dcm_resources (sqlalchemy.orm.query.Query): resource query to which
				the patient query filter should be applied.
			@input patient_tagname (str): tag which the query is associated with
			@input patient_queryfilter (str): query filter to apply

			@returns filtered query
		'''

	@abc.abstractmethod
	def apply_study_queryfilter(self, dcm_resources, study_tagname, study_queryfilter, *args, **kwargs):
		'''	Apply a study filter to the DICOM resource list

			@input dcm_resources (sqlalchemy.orm.query.Query): resource query to which
				the study query filter should be applied.
			@input study_tagname (str): tag with which the query is associated with
			@input study_queryfilter (str): query filter to apply

			@returns filtered query
		'''

	@abc.abstractmethod
	def apply_series_queryfilter(self, dcm_resources, series_tagname, series_queryfilter, *args, **kwargs):
		'''	Apply a series filter to the DICOM resource list

			@input dcm_resources (sqlalchemy.orm.query.Query): resource query to which the series
				query filter should be applied.
			@input series_tagname (str): tag with which the query is associated with
			@input series_queryfilter (str): query filter to apply

			@returns filtered query
		'''

	def execute_resource_query(self, session, *args, **kwargs):
		'''	Execute resource query
		'''
		# Filter results from cache
		resources = self.apply_session_options(session, self.get_base_resourcelist(session, *args, **kwargs))

		# Find "patient" resource query parameters
		for patient_tagname, patient_queryval in \
			pick(self.dicom_query, self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_PATIENT)).items():

			# Create PSQL regular expression from DICOM query
			patient_queryfilter = self.dcmquery2psqlregex(patient_queryval)
			logger.debug('Patient DICOM query component: tag="%s" dicom-query="%s" psql-regex="%s"'
				% (patient_tagname, patient_queryval, patient_queryfilter))

			# Apply patient filter to the query
			resources = self.apply_patient_queryfilter(resources, patient_tagname, patient_queryfilter)

		# Find "study" resource query parameters
		for study_tagname, study_queryval in \
			pick(self.dicom_query, self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_STUDY)).items():

			# Create PSQL regular expression from DICOM query
			study_queryfilter = self.dcmquery2psqlregex(study_queryval)
			logger.debug('Study DICOM query component: tag="%s" dicom-query="%s" psql-regex="%s"'
				% (study_tagname, study_queryval, study_queryfilter))

			# Apply study filter to the query
			resources = self.apply_study_queryfilter(resources, study_tagname, study_queryfilter)			

		# Find "series" resource query parameters
		for series_tagname, series_queryval in \
			pick(self.dicom_query, self.cache_dicomtags.get(IMAGING_SERVER_RESOURCE_SERIES)).items():

			# Create PSQL regular expression from DICOM query
			series_queryfilter = self.dcmquery2psqlregex(series_queryval)
			logger.debug('Series DICOM query component: tag="%s" dicom-query="%s" psql-regex="%s"'
				% (series_tagname, series_queryval, series_queryfilter))

			# Add regex to database filter
			resources = self.apply_series_queryfilter(resources, series_tagname, series_queryfilter)

		return resources

