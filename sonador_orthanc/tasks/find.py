''' Methods and utilities to help wtih DICOM C-FIND queries
'''
import logging, json, abc, copy
from abc import ABC
from collections import OrderedDict

from sqlalchemy.orm import joinedload

import orthanc

from client.errors import ConfigurationError
from client.utils.object import omit, pick

from sonador.apisettings import IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES, \
	DCMHEADER_SPECIFIC_CHARSET, DCMHEADER_QUERY_RETRIEVE_LEVEL, \
	DCMHEADER_MODALITIES_IN_STUDY, DCMHEADER_SERIES_DATE, DCMHEADER_STUDY_DATE
from sonador.serialization import SonadorJsonEncoder

from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..dcmquery import DicomQueryMixin, CacheStudyQueryMixin, CacheSeriesQueryMixin

logger = logging.getLogger(__name__)


class DicomCFindBaseCallback(ABC):
	'''	Class which can be used to process Orthanc C-FIND requests.
	'''
	query_level = None

	def __init__(self, *args, **kwargs):
		'''	Constructor method called at the time when a callback is registered. Provides 
			the ability for the callback to set extra keyword arguments and other parameters.
		'''
		for key,value in kwargs.items():
			setattr(self, key, value)

	@classmethod
	def as_callback(cls, **initkwargs):
		'''	Main entry point for a request/response process.
		'''
		for key in initkwargs:
			for key in initkwargs:
				
				# Only accept values that are already declared values of the class
				if not hasattr(cls, key):
					raise TypeError(
						"%s() received an invalid keyword %r. as_callback "
						"only accepts arguments that are already "
						"attributes of the class." % (cls.__name__, key)
					)

		def callback(answers, query, issuerAET, calledAET, *args, **kwargs):
			self = cls(**initkwargs)
			self.setup(answers, query, issuerAET, calledAET, *args, **kwargs)
			return self.execute_cfind(answers, query, issuerAET, calledAET, *args, **kwargs)

		callback.callback_class = cls
		callback.callback_initkwargs = initkwargs

		# __name__ and __qualname__ are intentionally left unchanged as view_class should
		# be used to robustly determine the name of the view instead.
		callback.__doc__ = cls.__doc__
		callback.__module__ = cls.__module__
		callback.__annotations__ = cls.execute_cfind.__annotations__

		# Copy possible attributes set by decorators from the dispatch method.
		callback.__dict__.update(cls.execute_cfind.__dict__)
		return callback

	def setup(self, answers, query, issuerAET, calledAET, *args, **kwargs):
		'''	Initialize attributes shared by all view methods.
		'''
		self.answers = answers
		self.query = query
		self.issuerAET = issuerAET
		self.calledAET = calledAET

		# Initialize DICOM query components
		self.dicom_query = self.parse_dcmquery(answers, query, issuerAET, calledAET, *args, **kwargs)
		self._init_dcmquery(answers, query, issuerAET, calledAET, *args, **kwargs)
		self.response_keys = self.parse_dcmresponse_keys(answers, query, issuerAET, calledAET, *args, **kwargs)

		logger.debug(
			'DICOM query (level=%s): %s' % (self.query_level, ', '.join('%s="%s"' % (k,v) for k,v in self.dicom_query.items())))

	def parse_dcmquery(self, answers, query, issuerAET, calledAET, *args, **kwargs):
		'''	Parse the DICOM query received by the server

			@returns dict: JSON structure of request
		'''
		dcmquery = OrderedDict((query.GetFindQueryTagName(i), query.GetFindQueryValue(i)) 
			for i in range(query.GetFindQuerySize()) if query.GetFindQueryValue(i))

		self.query_level = dcmquery.get(DCMHEADER_QUERY_RETRIEVE_LEVEL, '').lower()
		return dcmquery

	def parse_dcmresponse_keys(self, answers, query, issuerAET, calledAET, *args, **kwargs):
		'''	Parse the DICOM query received by the server to determine which keys need to be
			included in the response.

			@returns iterable of strings
		'''
		return tuple(query.GetFindQueryTagName(i) for i in range(query.GetFindQuerySize()))

	@abc.abstractmethod
	def execute_cfind(self, *args, **kwargs):
		''' Execute the C-FIND query			
		'''


class DicomCacheCFindStudyCallback(CacheStudyQueryMixin, DicomQueryMixin, DicomCFindBaseCallback):
	'''	Search the Sonador resource cache to find study resources which match the provided query.
	'''
	dbquery_omit_keys = (DCMHEADER_SPECIFIC_CHARSET, DCMHEADER_QUERY_RETRIEVE_LEVEL,
		DCMHEADER_MODALITIES_IN_STUDY, DCMHEADER_SERIES_DATE, DCMHEADER_STUDY_DATE)

	def parse_dcmquery(self, answers, query, issuerAET, calledAET, dcmquery=None):
		'''	Parse DICOM quer from modality to JSON structure, Omit study level and specific character set.
		'''
		# Skip parsing query if provided (method may be called )
		dcmquery = dcmquery or super().parse_dcmquery(answers, query, issuerAET, calledAET)

		# Retrieve special properties for study
		self.study_modalities = dcmquery.get(DCMHEADER_MODALITIES_IN_STUDY)
		self.study_date_filter = dcmquery.get(DCMHEADER_STUDY_DATE)
		self.series_date_filter = dcmquery.get(DCMHEADER_SERIES_DATE)

		# Omit black listed query keys
		return omit(dcmquery, self.dbquery_omit_keys)

	def apply_session_options(self, session, basequery):
		'''	Apply session options: load the related patient model alongside the study.
		'''
		return basequery.options(joinedload(self.resource_model.parent))

	def dcm_studyjson(self, cstudy):
		'''	Create JSON structure needed for JSON response
		'''
		dcm = copy.copy(cstudy.orthanc or {})

		# Add patient tags to study
		if cstudy.parent:
			dcm.update(copy.copy(cstudy.parent.orthanc or {}))

		return pick(dcm, self.response_keys)

	def execute_cfind(self, answers, query, issuerAET, calledAET, *args, **kwargs):
		'''	Execute C-FIND query
		'''
		with self.sessionmaker() as session:

			# If no query specified, return an empty response
			if not self.dicom_query:
				return

			# Retrieve study list
			for s in self.get_studylist(session):
				answers.FindAddAnswer(orthanc.CreateDicom(
					json.dumps(self.dcm_studyjson(s), cls=SonadorJsonEncoder), None, orthanc.CreateDicomFlags.NONE))


class DicomCacheCFindSeriesCallback(CacheSeriesQueryMixin, DicomQueryMixin, DicomCFindBaseCallback):
	'''	Search the Sonador resource cache to find series resources which match the provided query.
	'''
	dbquery_omit_keys = (DCMHEADER_SPECIFIC_CHARSET, DCMHEADER_QUERY_RETRIEVE_LEVEL,
		DCMHEADER_MODALITIES_IN_STUDY, DCMHEADER_SERIES_DATE, DCMHEADER_STUDY_DATE)

	def parse_dcmquery(self, answers, query, issuerAET, calledAET, dcmquery=None):
		'''	Parse DICOM query from modality to JSON structure. Omit study level and specific character set.
		'''
		# Skip parsing query if provided
		dcmquery = dcmquery or super().parse_dcmquery(answers, query, issuerAET, calledAET)

		# Retrieve special properties for series
		self.study_modalities = dcmquery.get(DCMHEADER_MODALITIES_IN_STUDY)
		self.study_date_filter = dcmquery.get(DCMHEADER_STUDY_DATE)
		self.series_date_filter = dcmquery.get(DCMHEADER_SERIES_DATE)

		# Omit black listed query keys		
		return omit(dcmquery, self.dbquery_omit_keys) if self.dbquery_omit_keys else dcmquery
		
	def apply_session_options(self, session, basequery):
		'''	Apply session options: load the related study and patient models alongside the series.
		'''
		return basequery.options(joinedload(CacheSeries.parent, CacheStudy.parent))

	def dcm_seriesjson(self, cseries):
		'''	Create JSON structure needed for DICOM response
		'''
		# Retrieve series tags
		dcm = copy.copy(cseries.orthanc or {}) 

		# Add study tags to series
		if cseries.parent:
			dcm.update(copy.copy(cseries.parent.orthanc or {}))

			# Add patient tags to series
			if cseries.parent.parent:
				dcm.update(copy.copy(cseries.parent.parent.orthanc or {}))

		return pick(dcm, self.response_keys)

	def execute_cfind(self, answers, query, issuerAET, calledAET, *args, **kwargs):
		'''	Execute C-FIND query
		'''
		with self.sessionmaker() as session:

			# If no query specified, return an empty response
			if not self.dicom_query:
				return

			# Retrieve all series which match the query
			for sx in self.get_serieslist(session):			
				answers.FindAddAnswer(orthanc.CreateDicom(
					json.dumps(self.dcm_seriesjson(sx), cls=SonadorJsonEncoder), None, orthanc.CreateDicomFlags.NONE))


class DicomCacheCFindCallback(DicomCFindBaseCallback):
	'''	Search the Sonador resource cache to find study resources which match the provided 
	'''
	study_query_class = DicomCacheCFindStudyCallback
	series_query_class = DicomCacheCFindSeriesCallback
	sessionmaker = None
	cache_dicomtags = None
	query_level = None

	def _init_dcmquery(self, *args, **kwargs):
		'''	Stub method required by DicomCFindBaseCallback interface
		'''
		pass

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)

		# Initialize series and study callbacks
		self.series_query_callback = self.series_query_class.as_callback(**kwargs)
		self.study_query_callback = self.study_query_class.as_callback(**kwargs)

	@classmethod
	def as_callback(cls, **initkwargs):
		study_query_class = initkwargs.get('study_query_class', cls.study_query_class)
		series_query_class = initkwargs.get('series_query_class', cls.series_query_class)

		# Ensure that study query and series query classs have been specified
		if not callable(study_query_class):
			raise ConfigurationError('Unable to initialize C-FIND callback, invalid study query class')
		if not callable(series_query_class):
			raise ConfigurationError('Unable to initialize C-FIND callback, invalid series query class')

		# Initialize callback classes
		callback = super(DicomCacheCFindCallback, cls).as_callback(
			**omit(initkwargs, ('study_query_class', 'series_query_class')))
		return callback

	def parse_dcmquery(self, answers, query, issuerAET, calledAET, *args, **kwargs):
		''' Parse DICOM query and determine which query class should be used to response.
		'''
		# Parse DICOM request, initialize query callback instance and use query callback
		# instance to parse the DICOM request. Return parsed query callback instance.
		dcmquery = super().parse_dcmquery(answers, query, issuerAET, calledAET)

		# Initialize query callback depending on the retrieve level of the query
		if self.query_level.lower() == IMAGING_SERVER_RESOURCE_SERIES.lower():
			self.query_callback = self.series_query_callback
		elif self.query_level.lower() == IMAGING_SERVER_RESOURCE_STUDY.lower():
			self.query_callback = self.study_query_callback
		else:
			raise ValueError('Unsupported query level: %s' % self.query_level)

		return dcmquery

	def execute_cfind(self, answers, query, issuerAET, calledAET, *args, **kwargs):
		'''	Execute C-FIND operation
		'''
		return self.query_callback(answers, query, issuerAET, calledAET, dcmquery=self.dicom_query)
