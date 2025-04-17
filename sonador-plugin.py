import six, os, json, logging, pprint, threading, itertools, requests, traceback, posixpath
import inspect, numbers
import orthanc

from confluent_kafka import Producer

from client.errors import ConfigurationError

from sonador.apisettings import DicomDatetimePairKey, \
	IMAGING_SERVER_RESOURCE_IMAGE, IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_PATIENT, \
	IMAGING_SERVER_RESOURCE_SUPPORTED, DCMHEADER_SERIES_INSTANCE_UID, DCMHEADER_STUDY_ID, DCMHEADER_PATIENT_ID, IMAGING_SERVER_UID_REGEX
from sonador.servers import SonadorServer, SonadorImagingServer

from sonador_orthanc_common.apisettings import ORTHANC_CONFIG_SECTION_DICT

from sonador_orthanc.apisettings import ORTHANC_CONFIG_SECTION_DICOMWEB, \
	ORTHANC_CONFIG_SECTION_POSTGRES, ORTHANC_CONFIG_SECTION_SONADOR, \
	ORTHANC_SERVER_ID as KTAG_ORTHANC_SERVER_ID, \
	ORTHANC_SERVER_RESOURCE as KTAG_ORTHANC_SERVER_RESOURCE, \
	ORTHANC_SERVER_SOURCE as KTAG_ORTHANC_SERVER_SOURCE, \
	ORTHANC_SERVER_DICOM as KTAG_ORTHANC_SERVER_DICOM, \
	ORTHANC_DEFAULT_ENCODING, \
	SONADOR_RESOURCE_UPDATE_PATIENT, SONADOR_RESOURCE_UPDATE_STUDY, SONADOR_RESOURCE_UPDATE_SERIES, \
	SONADOR_RESOURCE_DELETE_PATIENT, SONADOR_RESOURCE_DELETE_STUDY, SONADOR_RESOURCE_DELETE_SERIES, \
	SONADOR_CONF_PRIVATE_TAGS, SONADOR_CONF_DATETIME_TAGS, \
	SONADOR_CACHE_URL_ROOT, SONADOR_CACHE_TAGS_URL
from sonador_orthanc.helpers import init_sonador_server
from sonador_orthanc.manager import SonadorServerManager, \
	TIMER_30S, TIMER_MINUTE, TIMER_10MIN, TIMER_30MIN, TIMER_HOUR, TIMER_DAILY

logger = logging.getLogger(__name__)

KAFKA_TIMEOUT_DEFAULT = 10


orthanc.LogWarning('Sonador/Orthanc integration plugin enabled')


# Load configuration and extract API connection parameters
CONF = json.loads(orthanc.GetConfiguration())
CONF_SONADOR = CONF.get(ORTHANC_CONFIG_SECTION_SONADOR, {})
CONF_POSTGRESQL = CONF.get(ORTHANC_CONFIG_SECTION_POSTGRES, {})
CONF_DICOMWEB = CONF.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})

# Private DICOM Tags
CONF_DICOM_PRIVATEDICT = CONF.get(ORTHANC_CONFIG_SECTION_DICT, {})
CONF_DICOM_PRIVATEDICT['Tags'] = set(t[1] for t in CONF_DICOM_PRIVATEDICT.values())
CONF_DICOM_PRIVATETAGS = CONF.get(SONADOR_CONF_PRIVATE_TAGS, {})


# Kafka Configuration
CONF_KAFKA = CONF_SONADOR.get('Kafka', {})
if CONF_KAFKA:

	from sonador_orthanc import kafka

	# Initialize kafka producer
	KAFKA_PRODUCER = kafka.init_kafka_producer(CONF)

else: KAFKA_PRODUCER = None


# Initialize Sonador API client and check that all required authentication
# components are present (Sonador API clients should authenticate with API tokens)
if not CONF_SONADOR:
	raise ValueError('Invalid configuration, unable to locate Sonador section of configuration')

SONADOR_SERVER, ORTHANC_SONADOR_SERVERID = init_sonador_server(CONF_SONADOR)
ORTHANC_SONADOR_MANAGER = SonadorServerManager(SONADOR_SERVER, ORTHANC_SONADOR_SERVERID,
	conf=CONF_SONADOR, private_tags_dict=CONF_DICOM_PRIVATEDICT, kafka_producer=KAFKA_PRODUCER)

# Register/update Orthanc configuration with Sonador
ORTHANC_SONADOR_MANAGER.register_server()



# Sonador/Orthanc Integration: manage configured DICOM modalities and DICOMweb remotes.


# Retrieve Sonador configuration for the imaging server
from sonador_orthanc.helpers import init_fetch_sonador_configuration_callback

fetch_sonador_configuration = init_fetch_sonador_configuration_callback(ORTHANC_SONADOR_MANAGER)
ORTHANC_SONADOR_MANAGER.register_recurring_task(TIMER_10MIN, fetch_sonador_configuration)


# Initialize PostgreSQL Database Connections and Sonador Tables
if not CONF_POSTGRESQL:
	raise ConfigurationError('Unable to initialize Sonador / Orthanc plugin. Sonador integration '
		+ 'requires an external database and that the PostgreSQL extension be enabled.')


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



# Orthanc Server Event Handlers

if KAFKA_PRODUCER != None:

	from sonador_orthanc import kafka
	kafka.init(CONF, ORTHANC_SONADOR_MANAGER, OrthancSession)


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

def orthanc_cache_onstart(changeType, level, resource):
	'''	Initialize Sonador resource cache, endpoints, and scheduled tasks
	'''
	import sonador_orthanc.tasks.maintenance.cache as sonador_cache_maintenance
	import sonador_orthanc.db.cache as sonador_cachedb

	# DICOM Extension Tags
	CONF_DICOM_DATETIME_TAGS = CONF.get(SONADOR_CONF_DATETIME_TAGS, {})
	CONF_DICOM_DATETIME_TAGS['Tags'] = {}

	# Orthanc DICOM tags cache
	from sonador_orthanc.helpers import orthanc_maindicom_tags
	CACHE_DICOMTAGS = orthanc_maindicom_tags(CONF, dcm_privatetags=CONF_DICOM_PRIVATETAGS)

	# Ensure that all private tags in "PrivateMainDicomTags" have been registered with Orthanc.
	for ptag in itertools.chain(
		CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_PATIENT, []),
		CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_STUDY, []),
		CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_SERIES, [])):

		if not ptag in CONF_DICOM_PRIVATEDICT['Tags']:
			raise ConfigurationError(('Invalid configuration. Private tag "%s" included in PrivateMainDicomTags which is not '
				+ 'registered in the Orthanc Dictionary. Please refer to: https://oak-tree.tech/blog/soandor-orthanc-private-headers')
		 	% ptag)

	# Ensure that all date/time tags are included in the extra main DICOM tags
	for rtype,rdatetime_tags in CONF_DICOM_DATETIME_TAGS.items():

		if rtype in IMAGING_SERVER_RESOURCE_SUPPORTED:

			for dtags in rdatetime_tags:
				dtags = list(dtags.split(','))

				# Only a single tag defined (assume to be a date tag), add a "blank" string for the time
				if len(dtags) == 1:
					dtags[1] = ''

				# More than two components defined. Date/time tags should be of the form: DateTag,TimeTag
				elif len(dtags) > 2:
					raise ValueError(('Invalid %s configuration "%s". Datetime tags must be date values or date/time pairs.'
						+  'Examples: "SeriesDate", "SeriesDate,SeriesTime"' ) % (SONADOR_CONF_DATETIME_TAGS, ','.join(dtags)))

				dtmeta = DicomDatetimePairKey(rtype, *tuple(dtags))
				CONF_DICOM_DATETIME_TAGS['Tags'][dtmeta.date_tag] = dtmeta

				# Ensure that the date tag is registered in the API response tagset
				if not dtmeta.date_tag in CACHE_DICOMTAGS.get(dtmeta.resource, []):
					raise ConfigurationError('Invalid %s configuration. Tag "%s" (resource=%s) not configured for ExtraMainDicomTags.' % (
						SONADOR_CONF_DATETIME_TAGS, dtmeta.date_tag, dtmeta.resource,
					))

				if dtmeta.time_tag and not dtmeta.time_tag in CACHE_DICOMTAGS.get(dtmeta.resource, []):
					raise ConfigurationError('Invalid %s configuration. Tag "%s" (resource=%s) not configured for ExtraMainDicomTags.' % (
						SONADOR_CONF_DATETIME_TAGS, dtmeta.time_tag, dtmeta.resource,
					))


	# Enable DICOMweb endpoint overrides
	if CONF_DICOMWEB and CONF_DICOMWEB.get('Enable'):

		import sonador_orthanc.web.dicomweb as sonador_dicomweb

		# Initialize cached DICOMweb study list endpoint
		sonador_dicomweb.init_cached_endpoints(CONF, ORTHANC_SONADOR_MANAGER, OrthancSession)
		sonador_dicomweb.init_ext_endpoints(CONF, ORTHANC_SONADOR_MANAGER, OrthancSession)
		sonador_dicomweb.init_auth_endpoints(CONF, ORTHANC_SONADOR_MANAGER, OrthancSession)
		sonador_dicomweb.init_distortionfilter_endpoints(CONF, ORTHANC_SONADOR_MANAGER, OrthancSession)
		sonador_dicomweb.init_worklist_endpints(CONF, ORTHANC_SONADOR_MANAGER, OrthancSession)



	# Cache Query Endpoints
	from sonador_orthanc.web.patient import CachePatientQueryView, SonadorPatientResourceView
	from sonador_orthanc.web.study import CacheStudyQueryView, SonadorStudyResourceView
	from sonador_orthanc.web.series import CacheSeriesQueryView, SonadorSeriesResourceView
	from sonador_orthanc.web.secure_search import SecureToolsFindView

	orthanc.RegisterRestCallback('/cache/patients', CachePatientQueryView.as_view(
		sessionmaker=OrthancSession, cache_dicomtags=CACHE_DICOMTAGS, dcm_privatetags=CONF_DICOM_PRIVATETAGS,
		dcm_datetags=CONF_DICOM_DATETIME_TAGS))
	orthanc.RegisterRestCallback('/cache/studies', CacheStudyQueryView.as_view(
		sessionmaker=OrthancSession, cache_dicomtags=CACHE_DICOMTAGS, dcm_privatetags=CONF_DICOM_PRIVATETAGS,
		dcm_datetags=CONF_DICOM_DATETIME_TAGS))
	orthanc.RegisterRestCallback('/cache/series', CacheSeriesQueryView.as_view(
		sessionmaker=OrthancSession, cache_dicomtags=CACHE_DICOMTAGS, dcm_privatetags=CONF_DICOM_PRIVATETAGS,
		dcm_datetags=CONF_DICOM_DATETIME_TAGS))

	# Cache overrides of patient, study, and series
	orthanc.RegisterRestCallback(r'/patients/([0-9a-fA-F]{8}\-?){5}',
		SonadorPatientResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}',
		SonadorStudyResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(r'/series/([0-9a-fA-F]{8}\-?){5}',
		SonadorSeriesResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))

	# Cache override of /tools/find
	orthanc.RegisterRestCallback(r'/tools/secure-find', SecureToolsFindView.as_view(
		sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession, cache_dicomtags=CACHE_DICOMTAGS,
		dcm_privatetags=CONF_DICOM_PRIVATETAGS, dcm_datetags=CONF_DICOM_DATETIME_TAGS))


	# Comments
	from sonador_orthanc.web.comments import CommentSeriesManagementView, CommentSeriesRestView, CommentStudyManagementView, CommentStudyRestView

	orthanc.RegisterRestCallback(r'/series/([0-9a-fA-F]{8}\-?){5}/comments',
		CommentSeriesManagementView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(r'/series/([0-9a-fA-F]{8}\-?){5}/comments/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
		CommentSeriesRestView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}/comments',
		CommentStudyManagementView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}/comments/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
		CommentStudyRestView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))

	
	# Distortion filter
	from sonador_orthanc.web.distortionfilter import DistortionFilterDeviceManagementView, DistortionFilterDeviceRestView, DistortionFilterView

	orthanc.RegisterRestCallback(r'/distortion-filter/devices',
		DistortionFilterDeviceManagementView.as_view(sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(r'/distortion-filter/devices/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
		DistortionFilterDeviceRestView.as_view(sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(r'/distortion-filter/([0-9a-fA-F]{8}\-?){5}',
		DistortionFilterView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))
 
	# User Preferences
	from sonador_orthanc.web.preferences import UserPreferencesManagementView, UserPreferencesRestView
	
	orthanc.RegisterRestCallback(r'/user-preferences',
		UserPreferencesManagementView.as_view(sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(r'/user-preferences/([0-9a-fA-F]{8}\-?){5}',
		UserPreferencesRestView.as_view(sessionmaker=OrthancSession))

	# Tags
	from sonador_orthanc.web.tag import TagItemManagementView, TagItemRestView

	orthanc.RegisterRestCallback(r'/groups/[0-9]+/tags',
		TagItemManagementView.as_view(sessionmaker=OrthancSession, sonador_manager=ORTHANC_SONADOR_MANAGER))
	orthanc.RegisterRestCallback(r'/groups/[0-9]+/tags/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
		TagItemRestView.as_view(sessionmaker=OrthancSession, sonador_manager=ORTHANC_SONADOR_MANAGER))

	
	# Sonador authentication and access control endpoints
	from sonador_orthanc import auth as sonador_auth
	sonador_auth.init_auth(CONF, ORTHANC_SONADOR_MANAGER, OrthancSession)


	# Sonador worklists
	from sonador_orthanc import worklist as sonador_worklist
	sonador_worklist.init_reviewer_worklist(CONF, ORTHANC_SONADOR_MANAGER, OrthancSession)


	# Cache C-FIND handlers
	from sonador_orthanc.tasks.find import DicomCacheCFindCallback

	orthanc.RegisterFindCallback(DicomCacheCFindCallback.as_callback(
		sessionmaker=OrthancSession, cache_dicomtags=CACHE_DICOMTAGS))

	# Bulk Content Endpoints
	from sonador_orthanc.web.bulk import CacheFetchBulkContentView, SecureCacheFetchBulkContentView

	# DEPRECATED: Cache Bulk Content Endpoint (Requires admin access)
	orthanc.RegisterRestCallback(
		'/cache/tools/bulk-content', CacheFetchBulkContentView.as_view(sessionmaker=OrthancSession))

	# ACL mediated bulk content endpoint (Generally available, access mediated via ACL policy)
	orthanc.RegisterRestCallback(
		'/tools/bulk-content', SecureCacheFetchBulkContentView.as_view(
			sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))

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
		dcm_privatetags=CONF_DICOM_PRIVATETAGS, dcm_datetags=CONF_DICOM_DATETIME_TAGS))

	# Bulk index of patients, studies, series
	orthanc.RegisterRestCallback('/cache/admin/index/patients', sonador_cache.CacheBulkIndexPatientView.as_view(
		sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession, threadpool=tpool,
		dcm_privatetags=CONF_DICOM_PRIVATETAGS, dcm_datetags=CONF_DICOM_DATETIME_TAGS))
	orthanc.RegisterRestCallback('/cache/admin/index/studies', sonador_cache.CacheBulkIndexStudyView.as_view(
		sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession, threadpool=tpool,
		dcm_privatetags=CONF_DICOM_PRIVATETAGS, dcm_datetags=CONF_DICOM_DATETIME_TAGS))
	orthanc.RegisterRestCallback('/cache/admin/index/series', sonador_cache.CacheBulkIndexSeriesView.as_view(
		sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession, threadpool=tpool,
		dcm_privatetags=CONF_DICOM_PRIVATETAGS, dcm_datetags=CONF_DICOM_DATETIME_TAGS))

	# Individual index of patients, studies, series
	orthanc.RegisterRestCallback(
		r'/cache/patients/([0-9a-fA-F]{8}\-?){5}/index',
		sonador_cache.CacheIndexResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession,
			resource_cachemodel=sonador_cachedb.CachePatient, dcm_privatetags=CONF_DICOM_PRIVATETAGS, dcm_datetags=CONF_DICOM_DATETIME_TAGS))
	orthanc.RegisterRestCallback(
		r'/cache/studies/([0-9a-fA-F]{8}\-?){5}/index',
		sonador_cache.CacheIndexResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession,
			resource_cachemodel=sonador_cachedb.CacheStudy, dcm_privatetags=CONF_DICOM_PRIVATETAGS, dcm_datetags=CONF_DICOM_DATETIME_TAGS))
	orthanc.RegisterRestCallback(
		r'/cache/series/([0-9a-fA-F]{8}\-?){5}/index',
		sonador_cache.CacheIndexResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession,
			resource_cachemodel=sonador_cachedb.CacheSeries, dcm_privatetags=CONF_DICOM_PRIVATETAGS, dcm_datetags=CONF_DICOM_DATETIME_TAGS))

	# Reconstruct and index patients, studies, and series (replaces default reconstruct endpoint)
	orthanc.LogWarning('Register reconstruct/index endpoints for patient, study, and series')
	orthanc.RegisterRestCallback(
		r'/patients/([0-9a-fA-F]{8}\-?){5}/reconstruct',
		sonador_cache.CacheReconstructResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession,
			resource_cachemodel=sonador_cachedb.CachePatient, dcm_privatetags=CONF_DICOM_PRIVATETAGS, dcm_datetags=CONF_DICOM_DATETIME_TAGS))
	orthanc.RegisterRestCallback(
		r'/studies/([0-9a-fA-F]{8}\-?){5}/reconstruct',
		sonador_cache.CacheReconstructResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession,
			resource_cachemodel=sonador_cachedb.CacheStudy, dcm_privatetags=CONF_DICOM_PRIVATETAGS, dcm_datetags=CONF_DICOM_DATETIME_TAGS))
	orthanc.RegisterRestCallback(
		r'/series/([0-9a-fA-F]{8}\-?){5}/reconstruct',
		sonador_cache.CacheReconstructResourceView.as_view(sonador_manager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession,
			resource_cachemodel=sonador_cachedb.CacheSeries, dcm_privatetags=CONF_DICOM_PRIVATETAGS, dcm_datetags=CONF_DICOM_DATETIME_TAGS))


	# Cache indexing tasks

	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.NEW_PATIENT,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_patient, link=False,
			dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_PATIENT, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_PATIENT, CONF_DICOM_DATETIME_TAGS['Tags'].values()))))
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.STABLE_PATIENT,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_patient, link=True,
			dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_PATIENT, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_PATIENT, CONF_DICOM_DATETIME_TAGS['Tags'].values()))))
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		SONADOR_RESOURCE_UPDATE_PATIENT,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_patient, link=True,
			dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_PATIENT, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_PATIENT, CONF_DICOM_DATETIME_TAGS['Tags'].values()))))
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		SONADOR_RESOURCE_DELETE_PATIENT,
		sonador_cache_maintenance.remove_cache_serverchange_callback(
			ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cachedb.CachePatient))

	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.NEW_STUDY,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_study, link=False,
			dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_STUDY, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_STUDY, CONF_DICOM_DATETIME_TAGS['Tags'].values()))))
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.STABLE_STUDY,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_study, link=True,
			dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_STUDY, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_STUDY, CONF_DICOM_DATETIME_TAGS['Tags'].values()))))
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		SONADOR_RESOURCE_UPDATE_STUDY,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_study, link=True,
			dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_STUDY, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_STUDY, CONF_DICOM_DATETIME_TAGS['Tags'].values()))))
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		SONADOR_RESOURCE_DELETE_STUDY,
		sonador_cache_maintenance.remove_cache_serverchange_callback(
			ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cachedb.CacheStudy))

	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.NEW_SERIES,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_series, link=False,
			dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_SERIES, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_SERIES, CONF_DICOM_DATETIME_TAGS['Tags'].values()))))
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		orthanc.ChangeType.STABLE_SERIES,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_series, link=True,
			dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_SERIES, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_SERIES, CONF_DICOM_DATETIME_TAGS['Tags'].values()))))
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		SONADOR_RESOURCE_UPDATE_SERIES,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cache_maintenance.cache_index_series, link=True,
			dcm_privatetags=CONF_DICOM_PRIVATETAGS.get(IMAGING_SERVER_RESOURCE_SERIES, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_SERIES, CONF_DICOM_DATETIME_TAGS['Tags'].values()))))
	ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
		SONADOR_RESOURCE_DELETE_SERIES,
		sonador_cache_maintenance.remove_cache_serverchange_callback(
			ORTHANC_SONADOR_MANAGER, OrthancSession, sonador_cachedb.CacheSeries))	


ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
	orthanc.ChangeType.ORTHANC_STARTED, orthanc_cache_onstart)


def orthanc_sysinfo_onstart(changeType, level, resource):
	'''	Initialize Orthanc system info and status endpoints
	'''
	from sonador_orthanc.web.system import SonadorOrthancSystemReportView, SonadorOrthancSystemStatusView, \
		SonadorOrthancDicomTagsView

	orthanc.LogWarning('Enable Sonador/Orthanc system views')

	orthanc.RegisterRestCallback(
		'/system', SonadorOrthancSystemReportView.as_view(orthanc_conf=CONF, servermanager=ORTHANC_SONADOR_MANAGER))
	orthanc.RegisterRestCallback('/system/status', SonadorOrthancSystemStatusView.as_view(
			servermanager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(SONADOR_CACHE_TAGS_URL, SonadorOrthancDicomTagsView.as_view(
		servermanager=ORTHANC_SONADOR_MANAGER, sessionmaker=OrthancSession))


ORTHANC_SONADOR_MANAGER.register_serverchange_callback(
	orthanc.ChangeType.ORTHANC_STARTED, orthanc_sysinfo_onstart)
