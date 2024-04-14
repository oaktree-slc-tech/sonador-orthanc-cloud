'''	Orthanc views which help with the management of the resource cache.
'''
import posixpath, pydicom, logging, json, copy, datetime, traceback
import orthanc

from sqlalchemy.orm import joinedload

import client.apisettings as gcapicodes
import client.utils.apisettings as gcuapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist
from client.utils.conversion import str2bool
from client.utils.object import omit, pick

from sonador.apisettings import IMAGING_SERVER_UID_REGEX, DICOM_UID_REGEX, \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES
from sonador.serialization import SonadorJsonEncoder

from ..apisettings import \
	SONADOR_CACHE_STATUS_CURRENT, SONADOR_CACHE_STATUS_INCOMPLETE, SONADOR_CACHE_OPCODE_INDEX_RESOURCES, \
	SONADOR_CACHE_OPCODE_INDEX_PATIENT, SONADOR_CACHE_OPCODE_INDEX_STUDY, SONADOR_CACHE_OPCODE_INDEX_SERIES, \
	SONADOR_CACHE_OPCODE_INDEX_DELETE_PATIENT, SONADOR_CACHE_OPCODE_INDEX_DELETE_STUDY, \
	SONADOR_CACHE_OPCODE_INDEX_DELETE_SERIES, \
	SONADOR_CACHE_COUNT_PATIENT, SONADOR_CACHE_COUNT_STUDY, SONADOR_CACHE_COUNT_SERIES
from ..db.base import DbBase
from ..db.cache import CacheSeries, CacheStudy, CachePatient
from ..db.internal import Resource, DicomIdentifiers, \
    ORTHANCDB_PATIENT_TYPE, ORTHANCDB_STUDY_TYPE, ORTHANCDB_SERIES_TYPE
from ..db.comments import ImagingSeriesComment

from ..tasks.maintenance import cache_bulk_index_patients, cache_bulk_index_studies, cache_bulk_index_series, \
	cache_index_patient, cache_index_study, cache_index_series, remove_cache_resource

from .base import OrthancBaseView
from .helpers import paginate_query_results

logger = logging.getLogger(__name__)


IMAGING_CACHE_RESOURCES = {
	CachePatient: {
		'type': IMAGING_SERVER_RESOURCE_PATIENT, gcapicodes.OPCODE: SONADOR_CACHE_OPCODE_INDEX_PATIENT,
		'code': ORTHANCDB_PATIENT_TYPE, 'index_method': cache_index_patient,
		'code_delete': SONADOR_CACHE_OPCODE_INDEX_DELETE_PATIENT,
	},
	CacheStudy: {
		'type': IMAGING_SERVER_RESOURCE_STUDY, gcapicodes.OPCODE: SONADOR_CACHE_OPCODE_INDEX_STUDY,
		'code': ORTHANCDB_STUDY_TYPE, 'index_method': cache_index_study,
		'code_delete': SONADOR_CACHE_OPCODE_INDEX_DELETE_STUDY,
	},
	CacheSeries: {
		'type': IMAGING_SERVER_RESOURCE_SERIES, gcapicodes.OPCODE: SONADOR_CACHE_OPCODE_INDEX_SERIES,
		'code': ORTHANCDB_SERIES_TYPE, 'index_method': cache_index_series,
		'code_delete': SONADOR_CACHE_OPCODE_INDEX_DELETE_SERIES,
	},
}


class CacheBaseView(OrthancBaseView):
	'''	Base class for cache based views
	'''
	sonador_manager = None
	sessionmaker = None

	def setup(self, output, uri, request, *args, **kwargs):
		''' Setup view instance and ensure that sessionmaker instance is available.
		'''
		super().setup(output, uri, request)

		# Ensure that Sonador connection instance is present
		if self.sonador_manager is None:
			raise ConfigurationError(
				'Unable to initialize %s view instance: invalid Sonador connection' % type(self).__name__)

		# Ensure valid session maker instance is present
		if self.sessionmaker is None:
			raise ConfigurationError(
				'Unable to initialize %s view instance: invalid session maker instance' % type(self).__name__)


class ResourceBaseMixin(object):
	''' Mixin which provids methods to parse URLs and retrieve resources from the Orthanc database.
	'''
	resource_uid_regex = IMAGING_SERVER_UID_REGEX
	resource_model = Resource

	def init_resource_mixin(self, *args, **kwargs):
		'''	Verify that mixin properties, database models, and other attributes have been provided.
		'''
		if not self.resource_model:
			raise ConfigurationError(
				'Unable to initialize %s view instance: invalid resource model' % type(self).__name__)
		
		self.resource_type = kwargs.get('resource_type')
		self.resource_code = kwargs.get('resource_code')

		if not self.resource_type:
			raise ConfigurationError(
				'Unable to initialize %s (uri=%s) view instance, invalid resource type (type=%s).' 
					% (type(self).__name__, getattr(self, 'uri', None), self.resource_type))
		if self.resource_code is None:
			raise ConfigurationError(
				'Unable to initialize %s (uri=%s) view instance, invalid resource code (code=%s).' 
					% (type(self).__name__, getattr(self, 'uri', None), self.resource_code))

	def get_resource_uid(self, *args, resource_uri=None, **kwargs):
		''' Retrieve the UID of the DICOM resource from the URL

			@returns str or None: UID if there was a match, None otherwise
		'''
		resource_uri = resource_uri or self.uri
		ruid_match = self.resource_uid_regex.match(resource_uri)
		return ruid_match.groupdict().get('uid') if ruid_match else None

	def get_resource(self, session, ruid=None, *args, **kwargs):
		'''	Retrieve resource instance
		'''
		ruid = ruid or self.get_resource_uid(*args, **kwargs)
		r = session.query(self.resource_model).filter_by(resourcetype=self.resource_code, publicid=ruid).first()
		if not r:
			raise ResourceDoesNotExist(
				'Unable to retrieve retrieve resource model instance, uid=%s does not exist' % ruid,
				resource_details={ 'type': self.resource_type, 'uid': ruid })
		
		return r


class ResourceUidMixin(ResourceBaseMixin):
	'''	Mixin which provides integation methods between the Orthanc resources and the Sonador resource cache.
		(Inherits from ResourceBaseMixin.)
	'''
	resource_cachemodel = None
	imaging_cache_resources = IMAGING_CACHE_RESOURCES

	def init_resource_mixin(self, *args, **kwargs):
		'''	Initialize resource mixin properties from cache metadata
		'''
		if not self.resource_cachemodel:
			raise ConfigurationError(
				'Unable to initialize %s (uri=%s) view instance: invalid cache model' % (type(self).__name__), getattr(self, 'uri', None))
		if not self.imaging_cache_resources.get(self.resource_cachemodel):
			raise ConfigurationError(
				'Unable to initialize %s view (uri=%s) instance: unsupported cache model %s'
					% (type(self).__name__, getattr(self, 'uri', None), self.resource_cachemodel.__name__))

		self.resource_index_method = self.imaging_cache_resources.get(self.resource_cachemodel, {}).get('index_method')
		self.resource_opcode = self.imaging_cache_resources.get(self.resource_cachemodel, {}).get(gcapicodes.OPCODE)
		self.resource_opcode_delete = self.imaging_cache_resources.get(self.resource_cachemodel, {}).get('code_delete')

		super().init_resource_mixin(*args, resource_type=self.resource_cachemodel.type,
			resource_code=self.resource_cachemodel.code, **omit(kwargs, ('resource_type', 'resource_code')))


class CacheStatusBaseView(CacheBaseView):
	'''	Base view with endpoints to retrieve the status of the Sonador cache.

		1. Number of patients, studies, and series in the resources table.
		2. Number of patients, studies, and series in the Sonador cache tables.
	'''
	def cache_status_patient_count(self, session):
		'''	Retrieve status of patient tables in database and cache
		'''
		return {
			'db': session.query(Resource).filter_by(resourcetype=ORTHANCDB_PATIENT_TYPE).count(),
			'cache': session.query(CachePatient).count(),
		}

	def cache_status_study_count(self, session):
		'''	Retrieve status of study tables in database and cache
		'''
		return {
			'db': session.query(Resource).filter_by(resourcetype=ORTHANCDB_STUDY_TYPE).count(),
			'cache': session.query(CacheStudy).count(),
		}

	def cache_status_series_count(self, session):
		'''	Retrieve status of series tables in database and cache
		'''
		return {
			'db': session.query(Resource).filter_by(resourcetype=ORTHANCDB_SERIES_TYPE).count(),
			'cache': session.query(CacheSeries).count(),
		}


class CacheIndexResourceView(ResourceUidMixin, OrthancBaseView):
	'''	REST endpoint which can be used to place a copy of DICOM resource data in the Sonador cache.
	'''
	sonador_manager = None
	sessionmaker = None
	dcm_privatetags = None
	dcm_datetags = None

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Verify that database properties, database models, and indexing method have been provided.
		'''
		super().setup(output, uri, request)

		# Ensure that Sonador connection instance is present
		if self.sonador_manager is None:
			raise ConfigurationError(
				'Unable to initialize %s view instance: invalid Sonador connection' % type(self).__name__)

		# Ensure valid session maker instance is present
		if self.sessionmaker is None:
			raise ConfigurationError(
				'Unable to initialize %s view instance: invalid session maker instance' % type(self).__name__)

		# Ensure that a resource model has been defined and an index method is available
		self.init_resource_mixin(*args, **kwargs)

		# De-serialize request data and retrieve operation parameters
		self.POST = json.loads(request.get('body')) if request.get('body') else {}
		self.link = str2bool(self.POST.get('link', True))

	def post(self, output, uri, request, *args, **kwargs):
		'''	Add resource to the index cache 
		'''
		response = kwargs.get('response') or {}
		response.update({
				gcapicodes.OPCODE: self.resource_opcode,
				'Type': self.resource_type,
			})

		try:

			with self.sessionmaker() as session:

				# Ensure that the resource is defined 
				r = self.get_resource(session, *args, **kwargs)
				response['ID'] = r.publicid

				# Create copy of resource in cache
				self.resource_index_method(
					self.sonador_manager, session, r.publicid, link=self.link, 
					dcm_privatetags=(self.dcm_privatetags or {}).get(self.resource_type),
					dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == self.resource_type, (self.dcm_datetags or {}).get('Tags', {}).values())))
				response[gcapicodes.STATUS] = gcapicodes.SUCCESS

				return self.send_response(json.dumps(response, cls=SonadorJsonEncoder))
		
		except ResourceDoesNotExist as err:
			response.update({
				gcapicodes.ERROR: 'Resource uid=%s does not exist' \
					% self.get_resource_uid(*args, **kwargs) or '(none)',
				gcapicodes.STATUS: gcapicodes.FAIL
			})

			return self.http404_resource_not_found(response=response)

		except Exception as err:

			# Output error to application log
			emsg = 'Unable to index resource uid=%s. Error:\n%s' % (self.get_resource_uid(*args, **kwargs), err)
			logger.error('%s\n%s' % (emsg, traceback.format_exc()))

			# Return error message as part of response
			response.update({ gcapicodes.ERROR: emsg, gcapicodes.STATUS: gcapicodes.FAIL,})
			return self.send_response(json.dumps(response, cls=SonadorJsonEncoder), status_code=500)

	def delete(self, output, uri, request, *args, **kwargs):
		'''	Remove resource from the index cache
		'''
		response = kwargs.get('response') or {}
		response.update({
			'Type': self.resource_type, 
			gcapicodes.OPCODE: self.resource_opcode_delete
		})
		
		try:

			with self.sessionmaker() as session:

				# Ensure that the resource is defined
				r = self.get_resource(session, *args, **kwargs)
				response['ID'] = r.publicid

				# Remove resource from the cache
				cr = session.query(self.resource_cachemodel).get(r.publicid)
				if cr:

					# Remove the instance and other associated resources
					remove_cache_resource(self.sonador_manager, self.sessionmaker, self.resource_cachemodel,
						self.get_resource_uid(*args, **kwargs), commit=True)
					response[gcapicodes.STATUS] = gcapicodes.SUCCESS
				else:
					response[gcapicodes.STATUS] = gcapicodes.FAIL
					response[gcapicodes.ERROR] = 'Resource type=%s uid=%s not present in resource cache' \
						% (self.resource_type, r.publicid)

				return self.send_response(json.dumps(response, cls=SonadorJsonEncoder))

		except ResourceDoesNotExist as err:
			response.update({
				gcapicodes.ERROR: 'Resource uid=%s does not exist' \
					% self.get_resource_uid(*args, **kwargs) or '(none)',
			})

			# Check the Sonador cache to see if it is part of the local cache.
			# If so, remove the entry and all other associated data to prevent ghost entries.
			with self.sessionmaker() as session:
				_r = session.query(self.resource_cachemodel).filter_by(uid=self.get_resource_uid(*args, **kwargs)).first()
				if _r:
					remove_cache_resource(self.sonador_manager, self.sessionmaker, self.resource_cachemodel, _r.uid, commit=True)
					response[gcapicodes.STATUS] = gcapicodes.SUCCESS
					response[gcapicodes.ERROR] = ('Resource type=%s uid=%s not found in database but present in resource cache. '
						+ 'Cache instance has been removed.') % (self.resource_type, _r.uid)
			
			return self.http404_resource_not_found(response=response)

		except Exception as err:

			# Output error to application log
			emsg = 'Unable to remove resource uid=%s. Error\n%s' % (self.get_resource_uid(*args, **kwargs), err)
			logger.error('%s\n%s' % (emsg, traceback.format_exc()))

			# Return error as part of response
			response.update({ gcapicodes.ERROR: emsg, gcapicodes.STATUS: gcapicodes.FAIL })
			return self.send_response(json.dumps(response, cls=SonadorJsonEncoder), status_code=500)


class CacheBulkIndexBaseView(CacheStatusBaseView):
	'''	REST endpoint which can be used to add DICOM resources to the Sonador cache.
		Operations can be processed in the foreground or background.
	'''
	threadpool = None
	index_batch_size = 100	

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		# Ensure thread pool is available for background operations
		if self.threadpool is None:
			raise ConfigurationError(
				'Unable to initialize %s view: invalid threadpool instance' % type(self).__name__)

		# Parse request
		self.POST = json.loads(request.get('body')) if request.get('body') else {}
		self.limit = int(self.POST.get('Limit')) if self.POST.get('Limit') is not None else None
		self.offset = int(self.POST.get('Since')) if self.POST.get('Since') is not None else None


class CacheStatusView(CacheStatusBaseView):
	'''	REST endpoint which can be used to retrieve the status of the Sonador cache.
	'''
	def get(self, output, uri, request):
		''' Query cache tables and determine current status.
		'''
		with self.sessionmaker() as session:

			# Retrieve database and cache row counts for patient, studies, and series
			patient_count = self.cache_status_patient_count(session)
			study_count = self.cache_status_study_count(session)
			series_count = self.cache_status_series_count(session)
			
			response = {
				SONADOR_CACHE_COUNT_PATIENT: patient_count, 
				SONADOR_CACHE_COUNT_STUDY: study_count, 
				SONADOR_CACHE_COUNT_SERIES: series_count,
			}

			# Compare counts to determine the status of each resource table
			for cstatus in (patient_count, study_count, series_count):
				cstatus[gcapicodes.STATUS] = SONADOR_CACHE_STATUS_CURRENT if cstatus.get('db') == cstatus.get('cache') \
					else SONADOR_CACHE_STATUS_INCOMPLETE

			# Inspect record counts to determine overall status of the cache
			if all(map(lambda v: v.get(gcapicodes.STATUS) == SONADOR_CACHE_STATUS_CURRENT,
					(patient_count, study_count, series_count))):
				response[gcapicodes.STATUS] = SONADOR_CACHE_STATUS_CURRENT
			else:
				response[gcapicodes.STATUS] = SONADOR_CACHE_STATUS_INCOMPLETE
			
			return self.send_response(json.dumps(response))


class CacheBulkIndexPatientView(CacheBulkIndexBaseView):
	'''	REST endpoint which can be used to add patients to the Sonador cache.
	'''
	dcm_privatetags = None
	dcm_datetags = None

	def post(self, output, uri, request):
		'''	Execute indexing operations for patient cache
		'''
		# Parse request and verify config
		rdata = self.POST
		logger.warning('Begin bulk index of patients. Requested config: %s' % rdata)
		if not rdata.get(gcapicodes.OPCODE) == SONADOR_CACHE_OPCODE_INDEX_PATIENT:
			return self.send_response(json.dumps({
				gcapicodes.ERROR: 'Invalid %s request' % SONADOR_CACHE_OPCODE_INDEX_PATIENT,
			}))

		response = { gcapicodes.OPCODE: SONADOR_CACHE_OPCODE_INDEX_PATIENT }
		op_indexpatient_complete = lambda opresult: logger.warning('Bulk index of patient data complete')

		# TODO: Implement logic to allow for indexing a subset of patients

		# Kick off index operation as a worker process
		if str2bool(rdata.get('async', True)):
			response[gcapicodes.STATUS] = gcapicodes.INIT

			op_indexpatient = self.threadpool.submit(
				cache_bulk_index_patients, self.sonador_manager, self.sessionmaker, 
				batch_size=self.index_batch_size, limit=self.limit, offset=self.offset)
			op_indexpatient.add_done_callback(op_indexpatient_complete)

			return self.send_response(json.dumps(response))

		# Run index operation in foreground		
		opresult = cache_bulk_index_patients(self.sonador_manager, self.sessionmaker, 
			batch_size=self.index_batch_size, limit=self.limit, offset=self.offset)

		# Add operations results to response
		if opresult.success:
			op_indexpatient_complete(opresult)
			status_code = 200
			
			response[gcapicodes.STATUS] = gcapicodes.SUCCESS
			with self.sessionmaker() as session:
				response[SONADOR_CACHE_COUNT_PATIENT] = self.cache_status_patient_count(session)

		else:
			status_code = 500

			response[gcapicodes.STATUS] = gcapicodes.FAIL
			response[gcapicodes.ERROR] = '%s' % opresult.err

		return self.send_response(json.dumps(response), status_code=status_code)


class CacheBulkIndexStudyView(CacheBulkIndexBaseView):
	'''	REST endpoint which can be used to add studies to the Sonador cache.
	'''
	dcm_privatetags = None
	dcm_datetags = None

	def post(self, output, uri, request):
		'''	Execute indexing operations for study cache
		'''
		# Parse request and verify config
		rdata = self.POST
		logger.warning('Begin bulk index of studies. Requested config: %s' % rdata)
		if not rdata.get(gcapicodes.OPCODE) == SONADOR_CACHE_OPCODE_INDEX_STUDY:
			return self.send_response(json.dumps({
				gcapicodes.ERROR: 'Invalid %s request' % SONADOR_CACHE_OPCODE_INDEX_STUDY,
			}), status_code=400)

		response = { gcapicodes.OPCODE: SONADOR_CACHE_OPCODE_INDEX_STUDY }
		op_indexstudy_complete = lambda opresult: logger.warning('Bulk index of study data complete')

		# TODO: Implement logic to allow for indexing a subset of the studies on the server.

		# Kick off index operation as a worker process
		if str2bool(rdata.get('async', True)):
			response[gcapicodes.STATUS] = gcapicodes.INIT

			op_indexstudy = self.threadpool.submit(
				cache_bulk_index_studies, self.sonador_manager, self.sessionmaker, batch_size=self.index_batch_size,
				limit=self.limit, offset=self.offset)
			op_indexstudy.add_done_callback(op_indexstudy_complete)

			return self.send_response(json.dumps(response))

		# Run index operation in foreground
		opresult = cache_bulk_index_studies(self.sonador_manager, self.sessionmaker, batch_size=self.index_batch_size,
			limit=self.limit, offset=self.offset)

		# Add operations results to response
		if opresult.success:
			op_indexstudy_complete(opresult)
			status_code = 200

			response[gcapicodes.STATUS] = gcapicodes.SUCCESS
			with self.sessionmaker() as session:
				response[SONADOR_CACHE_COUNT_STUDY] = self.cache_status_study_count(session)

		else:
			status_code = 500

			response[gcapicodes.STATUS] = gcapicodes.FAIL
			response[gcapicodes.ERROR] = '%s' % opresult.err

		return self.send_response(json.dumps(response), status_code=status_code)


class CacheBulkIndexSeriesView(CacheBulkIndexBaseView):
	'''	REST endpoint which can be used to add DICOM series to the Sonador cache.
	'''
	dcm_privatetags = None
	dcm_datetags = None

	def post(self, output, uri, request):
		'''	Execute indexing operations for series cache
		'''
		# Parse request and verify config
		rdata = self.POST
		logger.warning('Begin bulk index of series. Requested config: %s' % rdata)
		if not rdata.get(gcapicodes.OPCODE) == SONADOR_CACHE_OPCODE_INDEX_SERIES:
			return self.send_response(json.dumps({
				gcapicodes.ERROR: 'Invalid %s request' % SONADOR_CACHE_OPCODE_INDEX_SERIES
			}), status_code=400)

		response = { gcapicodes.OPCODE: SONADOR_CACHE_OPCODE_INDEX_SERIES }
		op_indexseries_complete = lambda opresult: logger.warning('Bulk index of series data complete')

		# TODO: Implement logic to allow for indexing a subset of the studies on the server

		# Kick off index operation as a worker process
		if str2bool(rdata.get('async', True)):
			response[gcapicodes.STATUS] = gcapicodes.INIT

			op_indexseries = self.threadpool.submit(
				cache_bulk_index_series, self.sonador_manager, self.sessionmaker, batch_size=self.index_batch_size,
				limit=self.limit, offset=self.offset)
			op_indexseries.add_done_callback(op_indexseries_complete)

			return self.send_response(json.dumps(response))

		# Run index operation in foreground
		opresult = cache_bulk_index_series(self.sonador_manager, self.sessionmaker, batch_size=self.index_batch_size,
			limit=self.limit, offset=self.offset)

		# Add operation results to response
		if opresult.success:
			op_indexseries_complete(opresult)
			status_code = 200

			response[gcapicodes.STATUS] = gcapicodes.SUCCESS
			with self.sessionmaker() as session:
				response[SONADOR_CACHE_COUNT_SERIES] = self.cache_status_series_count(session)

		else:
			status_code = 500

			response[gcapicodes.STATUS] = gcapicodes.FAIL
			response[gcapicodes.ERROR] = '%s' % opresult.err

		return self.send_response(json.dumps(response), status_code=status_code)


class AdminRebuildCacheView(CacheBulkIndexBaseView):
	'''	REST endpoint which can be used to delete and re-initialize the Sonador cache tables.
	'''
	dbengine = None
	threadpool = None
	cache_tables = (CacheSeries, CacheStudy, CachePatient)
	index_batch_size = 100
	dcm_privatetags = None
	dcm_datetags = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		# Ensure database engine is available for administrative operations
		if self.dbengine is None:
			raise ConfigurationError('Unable to initialize rebuild cache view: invalid database engine instance.')

		# Ensure thread pool available for background operations
		if self.threadpool is None:
			raise ConfigurationError('Unable to initialize rebuild cache view: invalid threadpool instance')

	def post(self, output, uri, request):
		'''	Drop and re-initialize cache tables in the database
		'''
		# Parse request and verify config
		rdata = self.POST
		logger.warning('Rebuild Orthanc/Sonador Resource Cache. Requested config: %s' % rdata)
		if not rdata.get('rebuild'):
			return self.send_response(json.dumps({
				gcapicodes.ERROR: 'Invalid cache-rebuild request'
			}), status_code=400)

		response = { gcapicodes.OPCODE: 'cache-rebuild', gcapicodes.OPERATIONS: [] }

		# Execute re-build of cache tables
		try:
		
			# Drop tables in cache
			for ctable in self.cache_tables:
				ctable.__table__.drop(bind=self.dbengine)
				response[gcapicodes.OPERATIONS].append({
					gcapicodes.OPCODE: 'table-drop', 'table': ctable.__tablename__, gcapicodes.STATUS: gcapicodes.SUCCESS
				})

			# Re-create tables
			create_checkfirst = str2bool(rdata.get('checkfirst', True))
			DbBase.metadata.create_all(bind=self.dbengine, checkfirst=create_checkfirst)
			response[gcapicodes.OPERATIONS].append({
				gcapicodes.OPCODE: 'table-create', gcapicodes.STATUS: gcapicodes.SUCCESS,
			})

			# Truncate all the private tables and extended DICOM tables

			# Query new tables to ensure that they were re-initialized (and are empty)
			with self.sessionmaker() as session:
				response[SONADOR_CACHE_COUNT_PATIENT] =  self.cache_status_patient_count(session)
				response[SONADOR_CACHE_COUNT_STUDY] = self.cache_status_study_count(session)
				response[SONADOR_CACHE_COUNT_SERIES] = self.cache_status_series_count(session)

			# Initialize index operations
			if str2bool(rdata.get(SONADOR_CACHE_OPCODE_INDEX_RESOURCES, False)):

				# Because of how resource linking is implemented in the cache, bulk indexing of patients
				# must complete before indexing of studies can begin and must be finished
				# before indexing of series can be begin. Otherwise, the operations
				# will fail due to foreign key errors. Given the asynchronous organization
				# of the worker queue, scheduling callbacks are used upon completion of preceding
				# to invoke the indexing of the next step in the chain.

				# Indexing operations are compiled as callbacks in the reverse order 
				# from which they are scheduled: series -> study -> patient.

				def schedule_indexop_series(opresult_study):
					op_indexseries = self.threadpool.submit(
						cache_bulk_index_series, self.sonador_manager, self.sessionmaker, batch_size=self.index_batch_size)
					op_indexseries.add_done_callback(lambda opresult: logger.warning('Bulk index of series data complete'))

				def schedule_indexop_study(opresult_patient):
					op_indexstudy = self.threadpool.submit(
						cache_bulk_index_studies, self.sonador_manager, self.sessionmaker, batch_size=self.index_batch_size)
					op_indexstudy.add_done_callback(schedule_indexop_series)
					op_indexstudy.add_done_callback(lambda opresult: logger.warning('Bulk index of study data complete'))

				# Schedule patient indexing (first step in chain)
				op_indexpatient = self.threadpool.submit(
					cache_bulk_index_patients, self.sonador_manager, self.sessionmaker, batch_size=self.index_batch_size)
				op_indexpatient.add_done_callback(schedule_indexop_study)
				op_indexpatient.add_done_callback(lambda opresult: logger.warning('Bulk index of patient data complete'))

				response[gcapicodes.OPERATIONS].append({
					gcapicodes.OPCODE: SONADOR_CACHE_OPCODE_INDEX_RESOURCES, gcapicodes.STATUS: gcapicodes.INIT,
				})

			# Rebuild operation successful
			status_code = 200

		except Excetion as err:
			if not response.get(gcapicodes.ERRORS):
				response[gcapicodes.ERRORS] = []

			# Set operation code as "fail", send details on error
			response[gcapicodes.STATUS] = gcapicodes.FAIL
			response[gcapicodes.ERRORS].append({ gcapicodes.ERROR: '%s' % err })
			status_code = 500
		
		logger.warning('Rebuild of Orthanc/Sonador Resource Cache successful.')
		return self.send_response(json.dumps(response), status_code=status_code)


class CacheReconstructResourceView(ResourceUidMixin, CacheBaseView):
	'''	Orthanc reconstruct view which re-indexes resources after they have been rebuilt.
	'''	
	dcm_privatetags = None
	dcm_datetags = None

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Verify that database properties, database models, and indexing method have been provided.
		'''
		super().setup(output, uri, request)

		# Ensure that a resource model has been defined and an index method is available
		self.init_resource_mixin(*args, **kwargs)

		# De-serialize request data and retrieve operation parameters
		self.POST = json.loads(request.get('body')) if request.get('body') else {}
		self.link = str2bool(self.POST.get('link', True))

	def post(self, output, uri, request, *args, **kwargs):
		'''	Rebuild the specified resource and re-index
		'''
		response = kwargs.get('response') or { 'Type': self.resource_type }

		try:
			# Ensure that the resource exists before reconstructing
			with self.sessionmaker() as session:
				r = self.get_resource(session, *args, **kwargs)
				response['ID'] = r.publicid

				# Reconstruct resource
				rc = orthanc.RestApiPost(uri, json.dumps(pick(self.POST, ('ReconstructFiles',))))
				response.update({
					'reconstruct': json.loads(rc) if (rc and '{' in rc.encode('utf-8')) else {
						'reconstruct': { gcapicodes.OPCODE: 'reconstruct', gcapicodes.STATUS: gcapicodes.SUCCESS, }	
					}
				})

				# Re-index resource			
				self.resource_index_method(self.sonador_manager, session, r.publicid, link=self.link,
					dcm_privatetags=(self.dcm_privatetags or {}).get(self.resource_type),
					dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == self.resource_type, (self.dcm_datetags or {}).get('Tags', {}).values())))
				response.update({ 'index': {
						gcapicodes.OPCODE: self.resource_opcode,
						gcapicodes.STATUS: gcapicodes.SUCCESS,
					}
				})

			return self.send_response(json.dumps(response))

		except ResourceDoesNotExist as err:
			response.update({
				gcapicodes.ERROR: 'Resource uid=%s does not exist' \
					% self.get_resource_uid(*args, **kwargs) or '(none)',
				gcapicodes.STATUS: gcapicodes.FAIL,
			})
			return self.http404_resource_not_found(json.dumps(response, cls=SonadorJsonEncoder))

		except Exception as err:

			# Log error to application log
			logger.error('Unable to reconstruct resource uid=%s. Error="%s". Traceback:\n%s' % (
				self.get_resource_uid(*args, **kwargs), err, traceback.format_exc()
			))

			# Return error as part of response
			response.update({
				gcapicodes.ERROR: 'Unable to reconstruct resource uid=%s. Error:\n%s' \
					% (self.get_resource_uid(*args, **kwargs), err),
				gcapicodes.STATUS: gcapicodes.FAIL,
			})
			return self.send_response(json.dumps(response, cls=SonadorJsonEncoder), status_code=500)