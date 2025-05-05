'''	The Sonador Resource cache is a collection of database tables, views, and scheduled tasks
	that provide an optimized representation of DICOM resources. It is used by Sonador
	to provide extended functionality on the Orthanc core.

	The `init` method of this module can be used to initialize the cache, configure its
	background operations, and register its REST endpoints with Orthanc.
'''
from concurrent.futures import ThreadPoolExecutor as ThreadPool

from sonador.apisettings import IMAGING_SERVER_RESOURCE_IMAGE, IMAGING_SERVER_RESOURCE_SERIES, \
	IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_SUPPORTED
from sonador_orthanc_common.manager import TIMER_10S, TIMER_30S, TIMER_HOUR

from .. import apisettings as orthanc_api

from . import helpers as sonador_cachehelpers

from sonador.apisettings import IMAGING_SERVER_RESOURCE_SUPPORTED


def init(orthanc_config, sonador_manager, orthanc_sqlengine, sessionmaker,
		cache_dcmtags=None, conf_dcm_privatedict=None, conf_dcm_privatetags=None, conf_dcm_datetime_tags=None):
	'''	Initialize Sonador Resource cache
	'''
	import orthanc
	
	from ..db import cache as sonador_cachedb
	from ..tasks.maintenance import cache as sonador_cache_maintenance
	from . import web as sonador_cache

	# DICOM Tag Structures
	cache_dcmtags, conf_dcm_privatedict, conf_dcm_privatetags, conf_dcm_datetime_tags = sonador_cachehelpers.check_cache_tagconfig(
		orthanc_config, cache_dcmtags=cache_dcmtags, conf_dcm_privatedict=conf_dcm_privatedict, 
		conf_dcm_privatetags=conf_dcm_privatetags, conf_dcm_datetime_tags=conf_dcm_datetime_tags)	

	
	# Cache Query Endpoints
	from .web.patient import CachePatientQueryView, SonadorPatientResourceView
	from .web.study import CacheStudyQueryView, SonadorStudyResourceView
	from .web.series import CacheSeriesQueryView, SonadorSeriesResourceView
	from .web.secure_search import SecureToolsFindView

	orthanc.RegisterRestCallback('/cache/patients', CachePatientQueryView.as_view(
		sessionmaker=sessionmaker, cache_dicomtags=cache_dcmtags, dcm_privatetags=conf_dcm_privatetags,
		dcm_datetags=conf_dcm_datetime_tags))
	orthanc.RegisterRestCallback('/cache/studies', CacheStudyQueryView.as_view(
		sessionmaker=sessionmaker, cache_dicomtags=cache_dcmtags, dcm_privatetags=conf_dcm_privatetags,
		dcm_datetags=conf_dcm_datetime_tags))
	orthanc.RegisterRestCallback('/cache/series', CacheSeriesQueryView.as_view(
		sessionmaker=sessionmaker, cache_dicomtags=cache_dcmtags, dcm_privatetags=conf_dcm_privatetags,
		dcm_datetags=conf_dcm_datetime_tags))

	# Cache overrides of patient, study, and series
	orthanc.RegisterRestCallback(r'/patients/([0-9a-fA-F]{8}\-?){5}',
		SonadorPatientResourceView.as_view(sonador_manager=sonador_manager, sessionmaker=sessionmaker))
	orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}',
		SonadorStudyResourceView.as_view(sonador_manager=sonador_manager, sessionmaker=sessionmaker))
	orthanc.RegisterRestCallback(r'/series/([0-9a-fA-F]{8}\-?){5}',
		SonadorSeriesResourceView.as_view(sonador_manager=sonador_manager, sessionmaker=sessionmaker))

	
	# Cache override of /tools/find
	orthanc.RegisterRestCallback(r'/tools/secure-find', SecureToolsFindView.as_view(
		sonador_manager=sonador_manager, sessionmaker=sessionmaker, cache_dicomtags=cache_dcmtags,
		dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))

	
	# Bulk Content Endpoints
	from .web.bulk import CacheFetchBulkContentView, SecureCacheFetchBulkContentView

	# DEPRECATED: Cache Bulk Content Endpoint (Requires admin access)
	orthanc.RegisterRestCallback(
		'/cache/tools/bulk-content', CacheFetchBulkContentView.as_view(sessionmaker=sessionmaker))

	# ACL mediated bulk content endpoint (Generally available, access mediated via ACL policy)
	orthanc.RegisterRestCallback(
		'/tools/bulk-content', SecureCacheFetchBulkContentView.as_view(
			sonador_manager=sonador_manager, sessionmaker=sessionmaker))


	# Initialize thread pool for index operations
	from concurrent.futures import ThreadPoolExecutor as ThreadPool
	conf_sonador_cache = orthanc_config.get('Cache', {})
	cache_workers = conf_sonador_cache.get('CacheThreadsCount', 8)
	tpool = ThreadPool(max_workers=cache_workers)

	orthanc.LogWarning('Register Sonador Resource Cache endpoints')

	
	# Query cache status
	orthanc.RegisterRestCallback('/cache/admin/status', sonador_cache.CacheStatusView.as_view(
		sonador_manager=sonador_manager, sessionmaker=sessionmaker))

	# Rebuild resource cache
	orthanc.RegisterRestCallback('/cache/admin/rebuild', sonador_cache.AdminRebuildCacheView.as_view(
		sonador_manager=sonador_manager, dbengine=orthanc_sqlengine, sessionmaker=sessionmaker, threadpool=tpool,
		dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))

	# Bulk index of patients, studies, series
	orthanc.RegisterRestCallback('/cache/admin/index/patients', sonador_cache.CacheBulkIndexPatientView.as_view(
		sonador_manager=sonador_manager, sessionmaker=sessionmaker, threadpool=tpool,
		dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))
	orthanc.RegisterRestCallback('/cache/admin/index/studies', sonador_cache.CacheBulkIndexStudyView.as_view(
		sonador_manager=sonador_manager, sessionmaker=sessionmaker, threadpool=tpool,
		dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))
	orthanc.RegisterRestCallback('/cache/admin/index/series', sonador_cache.CacheBulkIndexSeriesView.as_view(
		sonador_manager=sonador_manager, sessionmaker=sessionmaker, threadpool=tpool,
		dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))
	orthanc.RegisterRestCallback('/cache/admin/index/instances', sonador_cache.CacheBulkIndexInstancesView.as_view(
		sonador_manager=sonador_manager, sessionmaker=sessionmaker, threadpool=tpool,
		dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))

	# Individual index of patients, studies, series
	orthanc.RegisterRestCallback(
		r'/cache/patients/([0-9a-fA-F]{8}\-?){5}/index',
		sonador_cache.CacheIndexResourceView.as_view(sonador_manager=sonador_manager, sessionmaker=sessionmaker,
			resource_cachemodel=sonador_cachedb.CachePatient, dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))
	orthanc.RegisterRestCallback(
		r'/cache/studies/([0-9a-fA-F]{8}\-?){5}/index',
		sonador_cache.CacheIndexResourceView.as_view(sonador_manager=sonador_manager, sessionmaker=sessionmaker,
			resource_cachemodel=sonador_cachedb.CacheStudy, dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))
	orthanc.RegisterRestCallback(
		r'/cache/series/([0-9a-fA-F]{8}\-?){5}/index',
		sonador_cache.CacheIndexResourceView.as_view(sonador_manager=sonador_manager, sessionmaker=sessionmaker,
			resource_cachemodel=sonador_cachedb.CacheSeries, dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))
	orthanc.RegisterRestCallback(
		r'/cache/instances/([0-9a-fA-F]{8}\-?){5}/index',
		sonador_cache.CacheIndexResourceView.as_view(sonador_manager=sonador_manager, sessionmaker=sessionmaker,
			resource_cachemodel=sonador_cachedb.CacheInstance, dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))

	# Reconstruct and index patients, studies, and series (replaces default reconstruct endpoint)
	orthanc.LogWarning('Register reconstruct/index endpoints for patient, study, and series')
	orthanc.RegisterRestCallback(
		r'/patients/([0-9a-fA-F]{8}\-?){5}/reconstruct',
		sonador_cache.CacheReconstructResourceView.as_view(sonador_manager=sonador_manager, sessionmaker=sessionmaker,
			resource_cachemodel=sonador_cachedb.CachePatient, dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))
	orthanc.RegisterRestCallback(
		r'/studies/([0-9a-fA-F]{8}\-?){5}/reconstruct',
		sonador_cache.CacheReconstructResourceView.as_view(sonador_manager=sonador_manager, sessionmaker=sessionmaker,
			resource_cachemodel=sonador_cachedb.CacheStudy, dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))
	orthanc.RegisterRestCallback(
		r'/series/([0-9a-fA-F]{8}\-?){5}/reconstruct',
		sonador_cache.CacheReconstructResourceView.as_view(sonador_manager=sonador_manager, sessionmaker=sessionmaker,
			resource_cachemodel=sonador_cachedb.CacheSeries, dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))
	orthanc.RegisterRestCallback(
		r'/instances/([0-9a-fA-F]{8}\-?){5}/reconstruct',
		sonador_cache.CacheReconstructResourceView.as_view(sonador_manager=sonador_manager, sessionmaker=sessionmaker,
			resource_cachemodel=sonador_cachedb.CacheInstance, dcm_privatetags=conf_dcm_privatetags, dcm_datetags=conf_dcm_datetime_tags))


	# Cache indexing tasks

	# Indexing of patient resources
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.NEW_PATIENT,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cache_maintenance.cache_index_patient, link=False,
			dcm_privatetags=conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_PATIENT, conf_dcm_datetime_tags['Tags'].values()))))
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.STABLE_PATIENT,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cache_maintenance.cache_index_patient, link=True,
			dcm_privatetags=conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_PATIENT, conf_dcm_datetime_tags['Tags'].values()))))
	sonador_manager.register_serverchange_callback(
		orthanc_api.SONADOR_RESOURCE_UPDATE_PATIENT,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cache_maintenance.cache_index_patient, link=True,
			dcm_privatetags=conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_PATIENT, conf_dcm_datetime_tags['Tags'].values()))))
	sonador_manager.register_serverchange_callback(
		orthanc_api.SONADOR_RESOURCE_DELETE_PATIENT,
		sonador_cache_maintenance.remove_cache_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cachedb.CachePatient))
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.DELETED,
		sonador_cache_maintenance.remove_cache_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cachedb.CachePatient, log_previously_deleted=False))

	# Indexing of study resources
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.NEW_STUDY,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cache_maintenance.cache_index_study, link=False,
			dcm_privatetags=conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_STUDY, conf_dcm_datetime_tags['Tags'].values()))))
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.STABLE_STUDY,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cache_maintenance.cache_index_study, link=True,
			dcm_privatetags=conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_STUDY, conf_dcm_datetime_tags['Tags'].values()))))
	sonador_manager.register_serverchange_callback(
		orthanc_api.SONADOR_RESOURCE_UPDATE_STUDY,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cache_maintenance.cache_index_study, link=True,
			dcm_privatetags=conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_STUDY, conf_dcm_datetime_tags['Tags'].values()))))
	sonador_manager.register_serverchange_callback(
		orthanc_api.SONADOR_RESOURCE_DELETE_STUDY,
		sonador_cache_maintenance.remove_cache_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cachedb.CacheStudy))
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.DELETED,
		sonador_cache_maintenance.remove_cache_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cachedb.CacheStudy, log_previously_deleted=False))

	# Indexing of series resources
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.NEW_SERIES,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cache_maintenance.cache_index_series, link=False,
			dcm_privatetags=conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_SERIES, conf_dcm_datetime_tags['Tags'].values()))))
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.STABLE_SERIES,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cache_maintenance.cache_index_series, link=True,
			dcm_privatetags=conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_SERIES, conf_dcm_datetime_tags['Tags'].values()))))
	sonador_manager.register_serverchange_callback(
		orthanc_api.SONADOR_RESOURCE_UPDATE_SERIES,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cache_maintenance.cache_index_series, link=True,
			dcm_privatetags=conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_SERIES, conf_dcm_datetime_tags['Tags'].values()))))
	sonador_manager.register_serverchange_callback(
		orthanc_api.SONADOR_RESOURCE_DELETE_SERIES,
		sonador_cache_maintenance.remove_cache_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cachedb.CacheSeries))
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.DELETED,
		sonador_cache_maintenance.remove_cache_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cachedb.CacheSeries, log_previously_deleted=False))

	# Indexing of instance resources
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.NEW_INSTANCE,
		sonador_cache_maintenance.cache_index_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cache_maintenance.cache_index_instance, link=False,
			dcm_privatetags=conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_IMAGE, []),
			dcm_datetags=tuple(filter(lambda dmeta: dmeta.resource == IMAGING_SERVER_RESOURCE_SERIES, conf_dcm_datetime_tags['Tags'].values()))))
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.DELETED,
		sonador_cache_maintenance.remove_cache_serverchange_callback(
			sonador_manager, sessionmaker, sonador_cachedb.CacheInstance, log_previously_deleted=False))

	# Synchronize patients, studies, series, and instances (startup)
	sonador_manager.create_scheduled_task(TIMER_10S,
		lambda: sonador_cache_maintenance.cache_syncdb_index_patients(sonador_manager, sessionmaker))
	sonador_manager.create_scheduled_task(TIMER_10S,
		lambda: sonador_cache_maintenance.cache_syncdb_index_studies(sonador_manager, sessionmaker))
	sonador_manager.create_scheduled_task(TIMER_10S,
		lambda: sonador_cache_maintenance.cache_syncdb_index_series(sonador_manager, sessionmaker))
	sonador_manager.create_scheduled_task(TIMER_10S,
		lambda: sonador_cache_maintenance.cache_syncdb_index_instances(sonador_manager, sessionmaker))

	
	def register_syncdb_schedule():
		'''	Register recurring tasks after startup
		'''
		sonador_manager.register_recurring_task(
			TIMER_HOUR, lambda: sonador_cache_maintenance.cache_syncdb_index_patients(sonador_manager, sessionmaker),force=True)
		sonador_manager.register_recurring_task(
			TIMER_HOUR, lambda: sonador_cache_maintenance.cache_syncdb_index_studies(sonador_manager, sessionmaker), force=True)
		sonador_manager.register_recurring_task(
			TIMER_HOUR, lambda: sonador_cache_maintenance.cache_syncdb_index_series(sonador_manager, sessionmaker), force=True)
		sonador_manager.register_recurring_task(
			TIMER_HOUR, lambda: sonador_cache_maintenance.cache_syncdb_index_instances(sonador_manager, sessionmaker), force=True)

	# Register 
	sonador_manager.create_scheduled_task(TIMER_30S, register_syncdb_schedule)
	
	
	return cache_dcmtags, conf_dcm_privatedict, conf_dcm_privatetags, conf_dcm_datetime_tags