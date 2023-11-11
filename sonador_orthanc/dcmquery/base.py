import logging, abc, datetime
from abc import ABC
from typing import Union, Sequence
from sqlalchemy import not_

from client.errors import ConfigurationError
from client.utils.object import omit, pick

from sonador.apisettings import \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES, \
	DCM_QUERY_ALLFIELDS, DCM_QUERY_NULL, DCM_QUERY_NOT_NULL, \
	IMAGING_SERVER_LAST_UPDATE, IMAGING_SERVER_MODIFIED
from sonador.serialization import dcm_str2date, dcm_str2datetime

from ..db.cache import CachePatient, CacheStudy, CacheSeries, SONADOR_CACHE_MODELS
from ..db.helpers import dcmquery2psqlregex, dcmquery_psqlregex_flags

logger = logging.getLogger(__name__)


class DicomQueryMixin(ABC):
	'''	Helper mixin which provides methods for translating DICOM queries into Sonador
		resource cache queries
	'''
	sessionmaker = None
	resource_model = None
	cache_dicomtags = None
	dcm_privatetags = None
	dcm_datetags = None
	dicom_query = None
	resource_mtime_query = None
	datetime_query_timedelta = datetime.timedelta(minutes=30)
	order_by = None

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

	def _clean_dcmdatestr(self, dcm_queryval, range_sep='-'):
		'''	Verify and clean (split and truncate) the provided query string into start/end components.
		'''
		rdate_startstr = rdate_stopstr = None

		# Split date string to start/end
		if range_sep in dcm_queryval:

			# Parse start and stop components
			rdate_components = dcm_queryval.split(range_sep)
			if not len(rdate_components) == 2:
				raise ValueError('Unable to parse DICOM date filter. Invalid range: %s'  % dcmquery_val)

			rdate_startstr, rdate_stopstr = rdate_components[0] if rdate_components[0] else None, rdate_components[1] if rdate_components[1] else None

		# Single value query, set "start" string only
		else: rdate_startstr = dcm_queryval

		return rdate_startstr, rdate_stopstr

	def _parse_dcmdate(self, dcmquery_val, range_sep='-'):
		'''	Parse a DICOM date string of type: 'yyyymmdd'. Supports:

			* single day values: yyyymmdd. Example: "20100101"
			* date ranges: "{start}-{end}", "yyyymmdd-yyyymmdd". Example: "20100101-20201231"
			* start from (open ended range): "{start}-", "yyyymmdd-"". Example: "20100101-"
			* end on (open ended range): "-{end}", "-yyyymmdd". Example: "-20201231"

			@input dcmquery_val (str): DCM string to be split

			@returns start_ts, end_ts
		'''
		rdate_start_ts = rdate_stop_ts = None

		# Parse date range
		if range_sep in dcmquery_val:

			# Parse start/stop from query value
			rdate_start, rdate_stop = self._clean_dcmdatestr(dcmquery_val, range_sep=range_sep)
			rdate_start = dcm_str2date(rdate_start) if rdate_start else None
			rdate_stop = dcm_str2date(rdate_stop) if rdate_stop else None

			# Parse to dates. For ranges with a start component (but not a stop component),
			# OHIF will add a value less than the start. If that is the case, set the stop component to be None.
			if (rdate_start and rdate_stop) and rdate_stop < rdate_start:
				rdate_stop = None

			if rdate_start:
				rdate_start_ts = datetime.datetime.combine(rdate_start, datetime.time(0,0,0))
			if rdate_stop:
				rdate_stop_ts = datetime.datetime.combine(rdate_stop, datetime.time(23,59,59))

			# Convert start/stop components to datetime and filter against timestamp of studies.
			# Filter is inclusive (midnight AM on start to midnight PM on stop).
			if rdate_start:
				rdate_start_ts = datetime.datetime.combine(rdate_start, datetime.time(0,0,0))				

			if rdate_stop:
				rdate_stop_ts = datetime.datetime.combine(rdate_stop, datetime.time(23,59,59))				
				
		# Match study date exactly. Because the CacheStudy.ts is a date/time, a range must
		# be used to match studies for the desired time period.
		else:
			rdate = dcm_str2date(dcmquery_val)
			rdate_start_ts = datetime.datetime.combine(rdate, datetime.time(0,0,0))
			rdate_stop_ts = datetime.datetime.combine(rdate, datetime.time(23,59,59))

		return rdate_start_ts, rdate_stop_ts

	def _parse_dcmdatetime(self, query_val, range_sep='-', timedelta=None):
		'''	Parse a DICOM date/time string of type 'yyyymmddHHMMSS'. Supports:

			* single timestamp values: yyyymmddHHMMSS. Example: "20100101123000"
			* date ranges: "{start}-{end}", "yyyymmddHHMMSS-yyyymmddHHMMSS". Example: "20100101-20201231"
			* start from (open ended range)
			* end on (open ended range)
		'''
		timedelta = timedelta or self.datetime_query_timedelta
		rdate_start_ts = rdate_stop_ts = None

		# Split date range into start/stop values
		if range_sep in query_val:
			rdate_start_ts, rdate_stop_ts = self._clean_dcmdatestr(query_val, range_sep=range_sep)
			rdate_start_ts = dcm_str2datetime(rdate_start_ts) if rdate_start_ts else None
			rdate_stop_ts = dcm_str2datetime(rdate_stop_ts) if rdate_stop_ts else None

		# Parse date/time as single value. As most date/time queries must use a range,
		# a start start and end time is calculated from the provided date/time +/- the timedelta.
		else:
			_ts = dcm_str2datetime(query_val)
			rdate_start_ts = _ts - timedelta
			rdate_stop_ts = _ts + timedelta

		return rdate_start_ts, rdate_stop_ts

	def parse_dcmdate_queryfilter(self, dcmquery_val: str, range_sep='-'):
		'''	Parse the provide DICOM query value to a start/end time for querying the resource cache. Both 
			DICOM date strings and DICOom date/time strings are supported.

			DICOM date strings have the form yyyymmdd with no spaces or dashes. DICOM date/time strings have the form 
			yyyymmddHHMMSS and may include a dash indicating a range of values.
		'''
		# For query values passed as a tuple/list, convert the query value to a single string
		if isinstance(dcmquery_val, (tuple, list)):

			# Ensure that the list only contains a single value
			if len(dcmquery_val) > 1:
				raise ValueError(('Invalid DICOM date: "%s". DICOM dates must be of the form: yyyymmdd (example: 20210721) ' 
					+ 'or yymmddHHMMSS (example: 20210721123000)')% str(dcmquery_val))
			
			# Pull value out of the list
			dcmquery_val = dcmquery_val[0]

		# Parse date range: check for single value, open ended query ("start from" or "end on"), or date range.
		# length changes are used in these checks as the structure of the request components will be validated
		# in the _parse_dcmdate method.
		if len(dcmquery_val) == 8 \
			or (range_sep in dcmquery_val and len(dcmquery_val) == 9) \
			or (range_sep in dcmquery_val and len(dcmquery_val) == 17):
			return self._parse_dcmdate(dcmquery_val, range_sep=range_sep)

		# Parse date/time range: check for single value, open ended query ("start from" or "end on"), or range.
		elif (len(dcmquery_val) == 14 and not range_sep in dcmquery_val) \
			or (range_sep in dcmquery_val and len(dcmquery_val) == 15) \
			or (range_sep in dcmquery_val and len(dcmquery_val) == 29):
			return self._parse_dcmdatetime(dcmquery_val, range_sep=range_sep)

		raise ValueError('Invalid DICOM date: %s' % str(dcmquery_val))

	def parse_multivalue_queryfilter(self, dcm_queryval, sep=r'\\', *args, **kwargs):
		''' Split a multi-value query filter into an iterable of conditions
		'''
		queryval = dcm_queryval

		if isinstance(queryval, str):
			queryval = queryval.replace(',', sep).split(sep)

		return queryval

	def _cache_queryfilter(self, cachemodel, tagname, queryfilter, **kwargs):
		'''	Create a query condition for the provided model, tag, and filter
		'''
		# Query for null values
		if queryfilter == DCM_QUERY_NULL:			

			# Key is present but empty OR key is present but undefined or falsy
			qc = not_(cachemodel.orthanc.has_key(tagname))
			qc |= cachemodel.orthanc[tagname] == None
			qc |= cachemodel.orthanc[tagname].astext == ''
			qc |= cachemodel.orthanc[tagname] == []
			return qc

		# Not null query: key is present and has a defined value
		elif queryfilter == DCM_QUERY_NOT_NULL:

			# Key is present with a valid value
			qc = cachemodel.orthanc.has_key(tagname)
			return qc

		return cachemodel.orthanc[tagname].astext.regexp_match(queryfilter, flags=dcmquery_psqlregex_flags(**kwargs))

	def _patient_querycondition(self, patient_tagname, patient_queryfilter, privatetags=False, **kwargs):
		'''	Create a patient query condition for the provided tag name and filter
		'''
		return self._cache_queryfilter(CachePatient.privatetags_resource_model if privatetags else CachePatient,
			patient_tagname, patient_queryfilter, **kwargs)

	def _study_querycondition(self, study_tagname, study_queryfilter, privatetags=False, **kwargs):
		'''	Create a study query condition for the provided tag name and filter
		'''
		return self._cache_queryfilter(CacheStudy.privatetags_resource_model if privatetags else CacheStudy,
			study_tagname, study_queryfilter, **kwargs)

	def _series_querycondition(self, series_tagname, series_queryfilter, privatetags=False, **kwargs):
		'''	Create a series query condition for the provided tag name and filter
		'''
		return self._cache_queryfilter(CacheSeries.privatetags_resource_model if privatetags else CacheSeries,
			series_tagname, series_queryfilter, **kwargs)

	def _querybuild_or(self, queryfilter, condition_builder, **kwargs):
		'''	Create an "OR" condition funciton from the provide query filter and condition builder method.

			@input queryfilter (dict): headers/values to use for building the OR query.
			@input condition_builder (callable): method to be called for creating new query conditions

			@returns condition function
		'''
		# Ensure that the condition builder is a valid callable
		if not callable(condition_builder):
			raise ValueError('Invalid query buidler function. Must be a callable object.')

		# Ensure that at least one conditions was provided in the query filter
		if not queryfilter:
			raise ValueError('Invalid query filter, at least one header and filter value must be provided')

		# Create OR query from tags and filter values
		query_or = None
		for tagname, queryval in queryfilter.items():
			if query_or is None: query_or = condition_builder(tagname, queryval, **kwargs)
			else: query_or |= condition_builder(tagname, queryval, **kwargs)

		return query_or

	def _patient_querycondition_or(self, patient_queryfilter, **kwargs):
		'''	Create a patient OR query

			@input patient_queryfilter (dict): headers/values to use for building the OR query

			@returns query conditions
		'''
		return self._querybuild_or(patient_queryfilter, self._patient_querycondition, **kwargs)

	def _study_querycondition_or(self, study_queryfilter, **kwargs):
		''' Create a study OR query

			@input study_queryfilter (dict): headers/values to use for building the OR query

			@returns query conditions
		'''
		return self._querybuild_or(study_queryfilter, self._study_querycondition, **kwargs)
		
	def _series_querycondition_or(self, series_queryfilter, **kwargs):
		'''	Create a series OR query

			@input series_queryfilter (dict): headers/values to use for building the OR 

			@return query conditions
		'''
		return self._querybuild_or(series_queryfilter, self._series_querycondition, **kwargs)

	@abc.abstractmethod
	def apply_allfields_queryfilter(self, dcm_resources, allfields_queryfilter, *args, **kwargs):
		'''	Apply an "all fields" filter to the DICOM resource list

			@input dcm_resources (sqlalchemy.orm.query.Query): resource query to which the
				all fields query filter should be applied.
			@input allfields_queryfilter (str): query filter to apply

			@returns filtered query
		'''

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
	def apply_series_queryfilter(self, dcm_resources, series_queryfilter, *args, **kwargs):
		'''	Apply a series filter to the DICOM resource list

			@input dcm_resources (sqlalchemy.orm.query.Query): resource query to which the series
				query filter should be applied.
			@input series_tagname (str): tag with which the query is associated with
			@input series_queryfilter (str): query filter to apply

			@returns filtered query
		'''

	def apply_ordering(self, dcm_resources, order_by: Sequence[str], **kwargs):
		'''	Apply ordering options to the DICOM resource query

			@returns ordered query
		'''
		orderby_fields = kwargs.get('orderby_fields') or []

		# Retrieve dataabase field for provided header
		for otag in order_by:

			# Determine whether ascending or descending for sort order
			if otag.startswith('-'):
				otag = otag.replace('-', '')
				desc = True
			else: desc = False

			# Determine the type of tag: resource level (Patient, Study, Series) and whether it is public/private
			resource_type = private_tag = None

			# Filter on resource mtime field (Modified or LastUpdate)
			if otag == IMAGING_SERVER_LAST_UPDATE or otag == IMAGING_SERVER_MODIFIED:

				# Set resource type and and resource model to prevent ordering on JSON field
				resource_type = self.resource_model.type
				private_tag = False

				orderby_fields.append(
					self.resource_model.mtime.desc() if desc else self.resource_model.mtime)

			# Filter on JSON fields (cache model or private cache model)
			if resource_type is None and private_tag is None:

				# Determine which level of the API at which to apply the header
				for rtype,rtags in self.cache_dicomtags.items():
					if otag in rtags:
						resource_type = rtype
						break

				# Determine if the header is public or private
				if self.dcm_privatetags:
					for rtype,ptags in self.dcm_privatetags.items():
						if otag in ptags:
							private_tag = True

				# Unable to locate private tag, mark as public
				if resource_type and private_tag is None:
					private_tag = False
				else:
					raise ValueError('Unable to order resources. Invalid DICOM header "%s".' % otag)

				# Retrieve tag model and add to ordering options
				tag_resource_model = SONADOR_CACHE_MODELS.get(resource_type)
				if private_tag:
					tag_resource_model = tag_resource_model.privatetags_resource_model			

				orderby_fields.append(
					tag_resource_model.orthanc[otag].astext.desc() if desc else tag_resource_model.orthanc[otag].astext)

		return dcm_resources.order_by(*tuple(orderby_fields))

	def execute_resource_query(self, session, *args, **kwargs):
		'''	Execute resource query
		'''
		# Filter results from cache
		resources = self.apply_session_options(session, self.get_base_resourcelist(session, *args, **kwargs))

		# Query all available fields
		if self.dicom_query.get(DCM_QUERY_ALLFIELDS):

			# Create PSQL regular expression from DICOM query and query all fields
			allfields_queryfilter = self.dcmquery2psqlregex(self.dicom_query.get(DCM_QUERY_ALLFIELDS))
			resources = self.apply_allfields_queryfilter(resources, allfields_queryfilter)

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

		# Apply "modified" filter
		if self.resource_mtime_query:
			rx_mtime_start, rx_mtime_stop = self.parse_dcmdate_queryfilter(self.resource_mtime_query)

			# Apply filters
			if rx_mtime_start:
				resources = resources.filter(self.resource_model.mtime >= rx_mtime_start)
			if rx_mtime_stop:
				resources = resources.filter(self.resource_model.mtime <= rx_mtime_stop)

		# Apply ordering
		if self.order_by:
			resources = self.apply_ordering(resources, self.order_by)

		return resources

