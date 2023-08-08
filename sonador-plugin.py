import six, os, json, logging, pprint, threading, itertools, requests, traceback, posixpath
import inspect, numbers
import orthanc

from confluent_kafka import Producer

from client.errors import ConfigurationError

from sonador.apisettings import IMAGING_SERVER_RESOURCE_IMAGE, IMAGING_SERVER_RESOURCE_SERIES, \
	IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_PATIENT, \
	DCMHEADER_SERIES_INSTANCE_UID, DCMHEADER_STUDY_ID, DCMHEADER_PATIENT_ID
from sonador.servers import SonadorServer, SonadorImagingServer

from sonador_orthanc.apisettings import ORTHANC_CONFIG_SECTION_DICOMWEB, \
	ORTHANC_CONFIG_SECTION_POSTGRES, ORTHANC_CONFIG_SECTION_SONADOR, \
	ORTHANC_SERVER_ID as KTAG_ORTHANC_SERVER_ID, \
	ORTHANC_SERVER_RESOURCE as KTAG_ORTHANC_SERVER_RESOURCE, \
	ORTHANC_SERVER_SOURCE as KTAG_ORTHANC_SERVER_SOURCE, \
	ORTHANC_SERVER_DICOM as KTAG_ORTHANC_SERVER_DICOM, \
	ORTHANC_DEFAULT_ENCODING, \
	SONADOR_RESOURCE_UPDATE_PATIENT, SONADOR_RESOURCE_UPDATE_STUDY, SONADOR_RESOURCE_UPDATE_SERIES, \
	SONADOR_RESOURCE_DELETE_PATIENT, SONADOR_RESOURCE_DELETE_STUDY, SONADOR_RESOURCE_DELETE_SERIES, \
	SONADOR_CONF_PRIVATE_DICT, SONADOR_CONF_PRIVATE_TAGS
from sonador_orthanc.helpers import init_sonador_server
from sonador_orthanc.manager import SonadorServerManager, \
	TIMER_30S, TIMER_MINUTE, TIMER_10MIN, TIMER_30MIN, TIMER_HOUR, TIMER_DAILY

logger = logging.getLogger(__name__)

KAFKA_TIMEOUT_DEFAULT = 10


# Background timers
CONFIG_TIMER = None


orthanc.LogWarning('Sonador/Orthanc integration plugin enabled')


# Load configuration and extract API connection parameters
CONF = json.loads(orthanc.GetConfiguration())
CONF_SONADOR = CONF.get(ORTHANC_CONFIG_SECTION_SONADOR, {})
CONF_POSTGRESQL = CONF.get(ORTHANC_CONFIG_SECTION_POSTGRES, {})
CONF_DICOMWEB = CONF.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})

# Private DICOM Tags
CONF_DICOM_PRIVATEDICT = CONF.get(SONADOR_CONF_PRIVATE_DICT, {})
CONF_DICOM_PRIVATEDICT['TagNames'] = set(t[1] for t in CONF_DICOM_PRIVATEDICT.values())
CONF_DICOM_PRIVATETAGS = CONF.get(SONADOR_CONF_PRIVATE_TAGS, {})

# Ensure that all private tags in "PrivateMainDicomTags" have been registered with Orthanc.
for ptag in itertools.chain(
	CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_PATIENT, []),
	CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_STUDY, []),
	CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_SERIES, [])):
	
	if not ptag in CONF_DICOM_PRIVATEDICT['TagNames']:
		raise ConfigurationError(('Invalid configuration. Private tag "%s" included in PrivateMainDicomTags which is not '
				+ 'registered in the Orthanc Dictionary. Please refer to: https://oak-tree.tech/blog/soandor-orthanc-private-headers')
			 % ptag)


# Initialize Sonador API client and check that all required authentication
# components are present (Sonador API clients should authenticate with API tokens)
if not CONF_SONADOR:
	raise ValueError('Invalid configuration, unable to locate Sonador section of configuration')

SONADOR_SERVER, ORTHANC_SONADOR_SERVERID = init_sonador_server(CONF_SONADOR)
ORTHANC_SONADOR_MANAGER = SonadorServerManager(SONADOR_SERVER, ORTHANC_SONADOR_SERVERID, conf=CONF_SONADOR)

# Register/update Orthanc configuration with Sonador
ORTHANC_SONADOR_MANAGER.register_server()


# Kafka Configuration
CONF_KAFKA = CONF_SONADOR.get('Kafka', {})
if CONF_KAFKA and CONF_KAFKA.get('servers'):
	KAFKA_SERVERS = ','.join(CONF_KAFKA.get('servers', [])) if isinstance(CONF_KAFKA.get('servers'), (tuple, list)) \
		else CONF_KAFKA.get('servers') if isinstance(CONF_KAFKA.get('servers'), six.string_types) \
		else None
	KAFKA_TOPIC = CONF_KAFKA.get('topic')

	if not KAFKA_SERVERS or not KAFKA_TOPIC:
		raise ValueError('Unable to initialize Kafka connection, invalid server list or topic')

	KAFKA_PRODUCER = Producer({ 'bootstrap.servers': KAFKA_SERVERS })

else: KAFKA_PRODUCER = None



# Sonador/Orthanc Integration: manage configured DICOM modalities and DICOMweb remotes


# Retrieve Sonador configuration for the imaging server
from sonador_orthanc.helpers import init_fetch_sonador_configuration_callback

fetch_sonador_configuration = init_fetch_sonador_configuration_callback(ORTHANC_SONADOR_MANAGER)
ORTHANC_SONADOR_MANAGER.register_recurring_task(TIMER_10MIN, fetch_sonador_configuration)


# Initialize PostgreSQL Database Connections and Sonador Tables
if CONF_POSTGRESQL and CONF_POSTGRESQL.get('EnableIndex'):

	from sonador_orthanc.helpers import init_postgresdb_conn
	from sonador_orthanc.db.base import DbBase, AutoDbBase

	# Initialize database connection
	ORTHANC_SQLENGINE, OrthancSession = init_postgresdb_conn(CONF_POSTGRESQL)

	
	def orthanc_db_onstart(changeType, level, resource):
		'''	Initialize database tables and AutoDb tables after server startup
		'''
		DbBase.metadata.create_all(bind=ORTHANC_SQLENGINE, checkfirst=True)
		AutoDbBase.prepare(autoload_with=ORTHANC_SQLENGINE)

	
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.ORTHANC_STARTED, orthanc_db_onstart)

else:
	ORTHANC_SQLENGINE = OrthancSession = None



# Orthanc Server Event Handlers

if KAFKA_PRODUCER != None:

	def orthanc_kafka_delivery_report(err, msg):
		'''	The Kafka producer delivers data asyncrhnously. This function is the
			callback by the Kafka client to indicate whether a message was delivered
			successfully or with an error. For successful deliveries, "err" will be None.

			@input err (exception, None for successful deliveries): Error report from the Kafka
				Producer client.
			@input msg (message instance): Message instance delivered to the Kafka broker
		'''
		if err is not None:
			orthanc.LogError('Unable to deliver message to Kafka instance %s. Error: %s\n%s' 
				% (err, KAFKA_SERVERS, msg.value()))
			KAFKA_PRODUCER.produce(KAFKA_TOPIC, msg.value(), callback=orthanc_kafka_delivery_report)


	def orthanc_kafka_export_instance_meta(dicom, instanceId):
		'''	Export DICOM instance metadata to Kafka
		'''
		# Create message structure
		idata = json.loads(dicom.GetInstanceSimplifiedJson())
		mdata = {
			KTAG_ORTHANC_SERVER_ID: ORTHANC_SONADOR_SERVERID,
			KTAG_ORTHANC_SERVER_RESOURCE: 'Instance',
			'ID': instanceId, 
			KTAG_ORTHANC_SERVER_SOURCE: 'DCM' if dicom.GetInstanceOrigin() == orthanc.InstanceOrigin.DICOM_PROTOCOL \
				else 'REST' if dicom.GetInstanceOrigin() == orthanc.InstanceOrigin.REST_API \
				else None,
			KTAG_ORTHANC_SERVER_DICOM: idata,
		}

		# Move patient, study, and series identifiers to the top-level
		if idata.get(DCMHEADER_PATIENT_ID):
			mdata[DCMHEADER_PATIENT_ID] = idata.get(DCMHEADER_PATIENT_ID)
		if idata.get(DCMHEADER_STUDY_ID):
			mdata[DCMHEADER_STUDY_ID] = idata.get(DCMHEADER_STUDY_ID)
		if idata.get(DCMHEADER_SERIES_INSTANCE_UID):
			mdata[DCMHEADER_SERIES_INSTANCE_UID] = idata.get(DCMHEADER_SERIES_INSTANCE_UID)
		
		orthanc.LogInfo('JSON data for DICOM instance:\n%r' % mdata)

		# Send to Kafka
		KAFKA_PRODUCER.produce(KAFKA_TOPIC, json.dumps(mdata), callback=orthanc_kafka_delivery_report)


	def orthanc_kafka_onstable_resource(changeType, level, resource):
		'''	Export data to Kafka about the DICOM resource marked as stable
		'''
		mdata = {
			KTAG_ORTHANC_SERVER_ID: ORTHANC_SONADOR_SERVERID,
			'ID': resource,
		}

		# Patient	
		if changeType == orthanc.ChangeType.STABLE_PATIENT:
			mdata[KTAG_ORTHANC_SERVER_RESOURCE] = IMAGING_SERVER_RESOURCE_PATIENT
			rdata = json.loads(orthanc.RestApiGet('/patients/%s' % resource))

		# Study
		elif changeType == orthanc.ChangeType.STABLE_STUDY:
			mdata[KTAG_ORTHANC_SERVER_RESOURCE] = IMAGING_SERVER_RESOURCE_STUDY
			rdata = json.loads(orthanc.RestApiGet('/studies/%s' % resource))

		# Series
		elif changeType == orthanc.ChangeType.STABLE_SERIES:
			mdata[KTAG_ORTHANC_SERVER_RESOURCE] = IMAGING_SERVER_RESOURCE_SERIES
			rdata = json.loads(orthanc.RestApiGet('/series/%s' % resource))

		# Add resource data to the message
		mdata[KTAG_ORTHANC_SERVER_DICOM] = rdata

		# Move patient, study, and series identifiers to the top-level
		if rdata.get(DCMHEADER_PATIENT_ID):
			mdata[DCMHEADER_PATIENT_ID] = rdata.get(DCMHEADER_PATIENT_ID)
		if rdata.get(DCMHEADER_STUDY_ID):
			mdata[DCMHEADER_STUDY_ID] = rdata.get(DCMHEADER_STUDY_ID)
		if rdata.get(DCMHEADER_SERIES_INSTANCE_UID):
			mdata[DCMHEADER_SERIES_INSTANCE_UID] = rdata.get(DCMHEADER_SERIES_INSTANCE_UID)

		# Send to Kafka
		KAFKA_PRODUCER.produce(KAFKA_TOPIC, json.dumps(mdata), callback=orthanc_kafka_delivery_report)


	def kafka_message_flush(poll_timeout=KAFKA_TIMEOUT_DEFAULT):
		'''	Flush messages to Kafka and retrieve transaction receipts
		'''
		try:
			logger.info('Push Kafka messages to broker: %s' % KAFKA_SERVERS)
			KAFKA_PRODUCER.poll(0)

		except Exception as err:
			logger.error('Unable to perform scheduled Kafka message flush due. Error: %s.\n%s'
				% (err, traceback.format_exc()))

	def orthanc_kafka_onstart(changeType, level, resource):
		'''	Initialize Orthanc/Kafka integration. Handle server state changes.

			@event: Initialize "poll" event loop to retrieve Kafka message receipts
				and flush messages to the Kafka broker.
			@event: Perform one final "flush" to allow for messages to be delieverd 
				and report callbacks to be triggered.
		'''
		# Initialize Sonador Kafka agent	
		orthanc.LogWarning('Start Orthanc/Kafka message scheduler')
		kafka_message_flush()


	def orthanc_kafka_onstop(changeType, level, resource):
		''' Turn off Orthanc/Kafka message scheduler and clear any pending messages
		'''
		# Stop Orthanc Kafka agent and flush all pending messages
		orthanc.LogWarning('Stop Orthanc/Kafka message scheduler')
		KAFKA_PRODUCER.flush()


	# Kafka start/stop callbacks
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.ORTHANC_STARTED, orthanc_kafka_onstart)
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.ORTHANC_STOPPED, orthanc_kafka_onstop)

	# Stable patient, study, and series events
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.STABLE_PATIENT, orthanc_kafka_onstable_resource)
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.STABLE_STUDY, orthanc_kafka_onstable_resource)
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.STABLE_SERIES, orthanc_kafka_onstable_resource)

	# Kafka scheduled events
	ORTHANC_SONADOR_MANAGER.register_recurring_task(TIMER_30S, kafka_message_flush)

	# Kafka storage callbacks
	orthanc.RegisterOnStoredInstanceCallback(orthanc_kafka_export_instance_meta)



# Server manager start/stop callbacks

def orthanc_sonador_onstart(changeType, level, resource):
	'''	Initialize Orthanc/Sonador inegration. Handle server state changes.

		@event startup: Initialize the server configuration and background timers.
	'''
	# Initialize Sonador remote configuration agent
	orthanc.LogWarning('Start Sonador Server Manager scheduler')
	try:
		fetch_sonador_configuration()
		ORTHANC_SONADOR_MANAGER.start()
	
	except Exception as err:
		logger.error('Unable to start Sonador server manager. Error:\n%s.\nTraceback:\n%s' 
			% (err, traceback.format_exc()))


def orthanc_sonador_onstop(changeType, level, resource):
	'''	Orthanc/Sonador integration teardown

		@event shutdown: Stop all background timers
	'''
	orthanc.LogWarning('Stop Sonador Server Manager scheduler')
	ORTHANC_SONADOR_MANAGER.stop()


ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
	orthanc.ChangeType.ORTHANC_STARTED, orthanc_sonador_onstart)
ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
	orthanc.ChangeType.ORTHANC_STOPPED, orthanc_sonador_onstop)


# Internal Sonador API REST endpoints

# Server Change Events. 2022-0813: the Python plugin API only supports
# "new" and "stable" change events. These endpoints provider trigger
# methods for "update" and "delete" events. The endpoints are called via
# a Lua script that implements the "update" and "delete" handlers.
from sonador_orthanc.web.events import OrthancEventView

orthanc.RegisterRestCallback('/sonador/internal/patient/change/(.*)', OrthancEventView.as_view(
	servermanager=ORTHANC_SONADOR_MANAGER, update_event_type=SONADOR_RESOURCE_UPDATE_PATIENT,
	delete_event_type=SONADOR_RESOURCE_DELETE_PATIENT, resource_class=orthanc.ResourceType.PATIENT))
orthanc.RegisterRestCallback('/sonador/internal/study/change/(.*)', OrthancEventView.as_view(
	servermanager=ORTHANC_SONADOR_MANAGER, update_event_type=SONADOR_RESOURCE_UPDATE_STUDY,
	delete_event_type=SONADOR_RESOURCE_DELETE_STUDY, resource_class=orthanc.ResourceType.STUDY))
orthanc.RegisterRestCallback('/sonador/internal/series/change/(.*)', OrthancEventView.as_view(
	servermanager=ORTHANC_SONADOR_MANAGER, update_event_type=SONADOR_RESOURCE_UPDATE_SERIES,
	delete_event_type=SONADOR_RESOURCE_DELETE_SERIES, resource_class=orthanc.ResourceType.SERIES))



# PostgreSQL Resource Cache: Enable API Routes and Resource Indexing

if CONF_POSTGRESQL and CONF_POSTGRESQL.get('EnableIndex'):

	def orthanc_cache_onstart(changeType, level, resource):
		'''	Initialize Sonador resource cache, endpoints, and scheduled tasks
		'''
		import sonador_orthanc.tasks.maintenance.cache as sonador_cache_maintenance
		import sonador_orthanc.db.cache as sonador_cachedb

		# Orthanc DICOM tags cache
		from sonador_orthanc.helpers import orthanc_maindicom_tags
		CACHE_DICOMTAGS = orthanc_maindicom_tags(CONF, dcm_privatetags=CONF_DICOM_PRIVATETAGS)

		# Enable DICOMweb endpoint overrides
		if CONF_DICOMWEB and CONF_DICOMWEB.get('Enable'):

			import sonador_orthanc.web.dicomweb as sonador_dicomweb

			# Initialize cached DICOMweb study list endpoint
			sonador_dicomweb.init_cached_endpoints(CONF, OrthancSession)

		# Cache Query Endpoints
		from sonador_orthanc.web.patient import CachePatientQueryView, SonadorPatientResourceView
		from sonador_orthanc.web.study import CacheStudyQueryView, SonadorStudyResourceView
		from sonador_orthanc.web.series import CacheSeriesQueryView, SonadorSeriesResourceView
		
		orthanc.RegisterRestCallback('/cache/patients', CachePatientQueryView.as_view(
			sessionmaker=OrthancSession, cache_dicomtags=CACHE_DICOMTAGS, dcm_privatetags=CONF_DICOM_PRIVATETAGS))
		orthanc.RegisterRestCallback('/cache/studies', CacheStudyQueryView.as_view(
			sessionmaker=OrthancSession, cache_dicomtags=CACHE_DICOMTAGS, dcm_privatetags=CONF_DICOM_PRIVATETAGS))
		orthanc.RegisterRestCallback('/cache/series', CacheSeriesQueryView.as_view(
			sessionmaker=OrthancSession, cache_dicomtags=CACHE_DICOMTAGS, dcm_privatetags=CONF_DICOM_PRIVATETAGS))

		# Cache overrides of patient, study, and series
		orthanc.RegisterRestCallback(r'/patients/([0-9a-fA-F]{8}\-?){5}',
			SonadorPatientResourceView.as_view(sessionmaker=OrthancSession))
		orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}',
			SonadorStudyResourceView.as_view(sessionmaker=OrthancSession))
		orthanc.RegisterRestCallback(r'/series/([0-9a-fA-F]{8}\-?){5}',
			SonadorSeriesResourceView.as_view(sessionmaker=OrthancSession))

		# Cache C-FIND handlers
		from sonador_orthanc.tasks.find import DicomCacheCFindCallback

		orthanc.RegisterFindCallback(DicomCacheCFindCallback.as_callback(
			sessionmaker=OrthancSession, cache_dicomtags=CACHE_DICOMTAGS))

		# Cache Bulk Content Endpoints
		from sonador_orthanc.web.bulk import CacheFetchBulkContentView

		orthanc.RegisterRestCallback(
			'/cache/tools/bulk-content', CacheFetchBulkContentView.as_view(sessionmaker=OrthancSession))


		# Initialize thread pool for index operations
		from concurrent.futures import ThreadPoolExecutor as ThreadPool
		CONF_SONADOR_CACHE = CONF_SONADOR.get('Cache', {})
		CACHE_WORKERS = CONF_SONADOR_CACHE.get('CacheThreadsCount', 4)
		tpool = ThreadPool(max_workers=CACHE_WORKERS)

		
		# Cache maintenance endpoints
		import sonador_orthanc.web.cache as sonador_cache
		orthanc.LogWarning('Register Sonador Resource Cache endpoints')

		# Query cache status
		orthanc.RegisterRestCallback('/cache/admin/status', sonador_cache.CacheStatusView.as_view(
			sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))

		# Rebuild resource cache
		orthanc.RegisterRestCallback('/cache/admin/rebuild', sonador_cache.AdminRebuildCacheView.as_view(
			sonador_manager=ORTHANC_SONADOR_MANAGER, dbengine=ORTHANC_SQLENGINE, sessionmaker=OrthancSession, threadpool=tpool,
			dcm_privatetags=CONF_DICOM_PRIVATETAGS))

		# Bulk index of patients, studies, series
		orthanc.RegisterRestCallback('/cache/admin/index/patients', sonador_cache.CacheBulkIndexPatientView.as_view(
			sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession, threadpool=tpool, 
			dcm_privatetags=CONF_DICOM_PRIVATETAGS))
		orthanc.RegisterRestCallback('/cache/admin/index/studies', sonador_cache.CacheBulkIndexStudyView.as_view(
			sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession, threadpool=tpool,
			dcm_privatetags=CONF_DICOM_PRIVATETAGS))
		orthanc.RegisterRestCallback('/cache/admin/index/series', sonador_cache.CacheBulkIndexSeriesView.as_view(
			sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession, threadpool=tpool,
			dcm_privatetags=CONF_DICOM_PRIVATETAGS))

		# Individual index of patients, studies, series
		orthanc.RegisterRestCallback(
			r'/cache/patients/([0-9a-fA-F]{8}\-?){5}/index', 
			sonador_cache.CacheIndexResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession,
				resource_cachemodel=sonador_cachedb.CachePatient, dcm_privatetags=CONF_DICOM_PRIVATETAGS))
		orthanc.RegisterRestCallback(
			r'/cache/studies/([0-9a-fA-F]{8}\-?){5}/index', 
			sonador_cache.CacheIndexResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession,
				resource_cachemodel=sonador_cachedb.CacheStudy, dcm_privatetags=CONF_DICOM_PRIVATETAGS))
		orthanc.RegisterRestCallback(
			r'/cache/series/([0-9a-fA-F]{8}\-?){5}/index', 
			sonador_cache.CacheIndexResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession,
				resource_cachemodel=sonador_cachedb.CacheSeries, dcm_privatetags=CONF_DICOM_PRIVATETAGS))

		# Reconstruct and index patients, studies, and series (replaces default reconstruct endpoint)
		orthanc.LogWarning('Register reconstruct/index endpoints for patient, study, and series')
		orthanc.RegisterRestCallback(
			r'/patients/([0-9a-fA-F]{8}\-?){5}/reconstruct',
			sonador_cache.CacheReconstructResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession,
				resource_cachemodel=sonador_cachedb.CachePatient, dcm_privatetags=CONF_DICOM_PRIVATETAGS))
		orthanc.RegisterRestCallback(
			r'/studies/([0-9a-fA-F]{8}\-?){5}/reconstruct',
			sonador_cache.CacheReconstructResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession,
				resource_cachemodel=sonador_cachedb.CacheStudy, dcm_privatetags=CONF_DICOM_PRIVATETAGS))
		orthanc.RegisterRestCallback(
			r'/series/([0-9a-fA-F]{8}\-?){5}/reconstruct',
			sonador_cache.CacheReconstructResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession,
				resource_cachemodel=sonador_cachedb.CacheSeries, dcm_privatetags=CONF_DICOM_PRIVATETAGS))


		# Cache indexing tasks

		ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
			orthanc.ChangeType.NEW_PATIENT, 
			sonador_cache_maintenance.cache_index_serverchange_callback(
				ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_patient, link=False,
				dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_PATIENT, [])))
		ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
			orthanc.ChangeType.STABLE_PATIENT,
			sonador_cache_maintenance.cache_index_serverchange_callback(
				ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_patient, link=True,
				dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_PATIENT, [])))
		ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
			SONADOR_RESOURCE_UPDATE_PATIENT,
			sonador_cache_maintenance.cache_index_serverchange_callback(
				ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_patient, link=True,
				dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_PATIENT, [])))
		ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
			SONADOR_RESOURCE_DELETE_PATIENT,
			sonador_cache_maintenance.remove_cache_serverchange_callback(
				ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cachedb.CachePatient))

		ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
			orthanc.ChangeType.NEW_STUDY, 
			sonador_cache_maintenance.cache_index_serverchange_callback(
				ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_study, link=False,
				dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_STUDY, [])))
		ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
			orthanc.ChangeType.STABLE_STUDY,
			sonador_cache_maintenance.cache_index_serverchange_callback(
				ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_study, link=True,
				dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_STUDY, [])))
		ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
			SONADOR_RESOURCE_UPDATE_STUDY,
			sonador_cache_maintenance.cache_index_serverchange_callback(
				ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_study, link=True,
				dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_STUDY, [])))
		ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
			SONADOR_RESOURCE_DELETE_STUDY,
			sonador_cache_maintenance.remove_cache_serverchange_callback(
				ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cachedb.CacheStudy))
		
		ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
			orthanc.ChangeType.NEW_SERIES, 
			sonador_cache_maintenance.cache_index_serverchange_callback(
				ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_series, link=False,
				dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_SERIES, [])))
		ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
			orthanc.ChangeType.STABLE_SERIES,
			sonador_cache_maintenance.cache_index_serverchange_callback(
				ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_series, link=True,
				dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_SERIES, [])))
		ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
			SONADOR_RESOURCE_UPDATE_SERIES,
			sonador_cache_maintenance.cache_index_serverchange_callback(
				ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_series, link=True,
				dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_SERIES, [])))
		ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
			SONADOR_RESOURCE_DELETE_SERIES,
			sonador_cache_maintenance.remove_cache_serverchange_callback(
				ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cachedb.CacheSeries))


	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.ORTHANC_STARTED, orthanc_cache_onstart)


	def orthanc_worklist_onstart(changeType, level, resource):
		'''	Initialize Orthanc worklist models, endpoints, and scheduled tasks
		'''
		from sonador_orthanc.web.worklist import ProcedureStepManagementView

		logger.critical('Enable worklist management view')

		orthanc.RegisterRestCallback(
			'/sonador/worklist', ProcedureStepManagementView.as_view(sessionmaker=OrthancSession))


	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.ORTHANC_STARTED, orthanc_worklist_onstart)


	def orthanc_sysinfo_onstart(changeType, level, resource):
		'''	Initialize Orthanc system info and status endpoints
		'''
		from sonador_orthanc.web.system import SonadorOrthancSystemReportView, SonadorOrthancSystemStatusView

		logger.critical('Enable Sonador/Orthanc system views')

		orthanc.RegisterRestCallback(
			'/system', SonadorOrthancSystemReportView.as_view(orthanc_conf=CONF, servermanager=ORTHANC_SONADOR_MANAGER))
		orthanc.RegisterRestCallback(
			'/system/status', SonadorOrthancSystemStatusView.as_view(servermanager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))


	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.ORTHANC_STARTED, orthanc_sysinfo_onstart)