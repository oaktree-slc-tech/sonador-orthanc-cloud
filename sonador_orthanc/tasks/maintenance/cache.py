'''	Task methods for Orthanc maintenance
'''
import logging, json, posixpath, traceback
from collections import namedtuple
import orthanc

from sonador.servers import SonadorServer
from sonador.imaging.orthanc import ImagingPatient, ImagingStudy, ImagingSeries

from ...db.internal import Resource, \
    ORTHANCDB_PATIENT_TYPE, ORTHANCDB_STUDY_TYPE, ORTHANCDB_SERIES_TYPE, ORTHANCDB_INSTANCE_TYPE
from ...db.cache import CachePatient, CacheStudy, CacheSeries, CacheInstance
from ...db.dcmext import CachePatientPrivateTags, CacheStudyPrivateTags, CacheSeriesPrivateTags
from ...db.helpers import cache_index_missing_resources, cache_index_ghosts

from ...manager import SonadorServerManager
from ...helpers import orthancserver_get_patient, orthancserver_get_study, orthancserver_get_series, \
	orthancserver_get_instance
from ...web.helpers import paginate_query_results

logger = logging.getLogger(__name__)


OpResults = namedtuple('OpResults', ('success', 'count', 'err'))


def cache_index_patient(sonador_manager: SonadorServerManager, session, uid, commit=True, **kwargs):
	'''	Add a patient to the Sonador cache

		@returns tuple: CachePatient, ImagingPatient
	'''
	p = orthancserver_get_patient(sonador_manager, uid)
	return CachePatient.index(session, p, commit=commit, **kwargs), p


def cache_bulk_index_patients(sonador_manager: SonadorServerManager, sessionmaker, db_patients=None, batch_size=100, limit=None, offset=None, **kwargs):
	'''	Bulk import of DICOM patient data into the Sonador resource cache
	'''
	try:
		opcount = 0

		with sessionmaker() as session:
			db_patients = db_patients or session.query(Resource).filter_by(resourcetype=ORTHANCDB_PATIENT_TYPE)
			db_patients_count = len(paginate_query_results(db_patients, offset, limit)) if (limit or offset) \
				else db_patients.count()
			logger.warning('Begin bulk index of DICOM patient data: queue-size="%s"' % db_patients_count)

			for r_db in (paginate_query_results(db_patients, offset, limit) if (limit or offset) else db_patients):

				# Index patient, increment batch counter
				cp, p = cache_index_patient(sonador_manager, session, r_db.publicid, commit=False, **kwargs)
				opcount += 1
				
				# Commit records to database at end of batch
				if opcount % batch_size == 0:
					session.commit()
					logger.warning('Bulk DICOM patient data committed to resource cache: progress=%s/%s' % (opcount, db_patients_count))

				logger.debug('Import of patient (uid="%s") to cache successful: patient-id="%s" patient-name="%s"' 
					% (p.pk, p.patientid, p.patient_name))

			session.commit()
			session.flush()

		return OpResults(True, opcount, None)
	
	except Exception as err:
		logger.error('Unable to complete indexing of patient records. Error:\n%s' % err)
		return OpResults(False, opcount, err)


def cache_syncdb_index_patients(sonador_manager: SonadorServerManager, sessionmaker, db_patients=None, cache_patients_ghosts=None, 
		batch_size=100, limit=1000, offset=0, **kwargs):
	'''	Synchronize Orthanc's resource database with the Sonador Resource cache.

		1. Patient records present in the Resources, but not in CachePatient table are propagated to the cache.
		2. Patient records present in CachePatient, but no longer in Resources are pruned.
	'''
	with sessionmaker() as session:

		# Determine missing patients and ghosted patients
		db_patients = db_patients or cache_index_missing_resources(session, CachePatient)
		cache_patients_ghosts = cache_patients_ghosts or cache_index_ghosts(session, CachePatient)

		logger.warning('Synchronize DICOM patients resources to Sonador resource cache: missing-patients="%s" ghosted-patients="%s"' % (
			db_patients.count(), cache_patients_ghosts.count(),
		))

		# Bulk index patients
		if db_patients.count():
			cache_bulk_index_patients(sonador_manager, sessionmaker, db_patients=db_patients,
				batch_size=batch_size, limit=limit, offset=offset, **kwargs)

		# Remove ghosted patient records (records present in cache, but no longer on the server)
		for g in cache_patients_ghosts.all():
			remove_cache_resource(sonador_manager, sessionmaker, CachePatient, g.uid)


def cache_index_study(sonador_manager: SonadorServerManager, session, uid, commit=True, link=True, **kwargs):
	'''	Add a study to the Sonador cache

		@returns tuple: CacheStudy, ImagingStudy
	'''
	s = orthancserver_get_study(sonador_manager, uid)
	return CacheStudy.index(session, s, commit=commit, link=link, **kwargs), s


def cache_bulk_index_studies(sonador_manager: SonadorServerManager, sessionmaker, db_studies=None, batch_size=100, limit=None, offset=None, **kwargs):
	'''	Bulk import of  DICOM study data into the Sonador resource cache

		@returns bool: True if the operation completes successfully, False if there was an error
	'''
	opcount = 0

	try:
		

		with sessionmaker() as session:
			db_studies = db_studies or session.query(Resource).filter_by(resourcetype=ORTHANCDB_STUDY_TYPE)
			db_studies_count = len(paginate_query_results(db_studies, offset, limit)) if (limit or offset) \
				else db_studies.count()
			logger.warning('Begin bulk index of DICOM study data: queue-size="%s"' % db_studies_count)

			for r_db in (paginate_query_results(db_studies, offset, limit) if (limit or offset) else db_studies):

				# Index study, increment batch counter
				sp, s = cache_index_study(sonador_manager, session, r_db.publicid, commit=False, **kwargs)
				opcount += 1

				# Commit records to database at end of batch
				if opcount % batch_size == 0:
					session.commit()
					logger.warning('Bulk DICOM study data committed to resource cache: progress=%s/%s' % (opcount, db_studies_count))

				logger.debug('Import of study (uid=%s) to cache successful: patient="%s" series-count="%s"' 
					% (s.pk, s.patient, len(s.series_collection)))

			session.commit()
			session.flush()

		return OpResults(True, opcount, None)

	except Exception as err:
		logger.error('Unable to complete indexing of study records. Error:\n%s' % err)
		return OpResults(False, opcount, err)


def cache_syncdb_index_studies(sonador_manager: SonadorServerManager, sessionmaker, db_studies=None, cache_study_ghosts=None, 
		batch_size=100, limit=1000, offset=0, **kwargs):
	'''	Synchronize Orthanc's resource database with the Sonador Resource cache.

		1. Study records present in the Resources, but not in CacheStudy table are propagated to the cache.
		2. Study records present in CacheStudy, but no longer in Resources are pruned.
	'''
	with sessionmaker() as session:

		# Determine missing studies and ghosted studies
		db_studies = db_studies or cache_index_missing_resources(session, CacheStudy)
		cache_study_ghosts = cache_study_ghosts or cache_index_ghosts(session, CacheStudy)

		logger.warning('Synchronize DICOM study resources to Sonador resource cache: missing-studies="%s" ghosted-studies="%s"' % (
			db_studies.count(), cache_study_ghosts.count(),
		))

		# Bulk index patients
		if db_studies.count():
			cache_bulk_index_studies(sonador_manager, sessionmaker, db_studies=db_studies,
				batch_size=batch_size, limit=limit, offset=offset, **kwargs)

		# Remove ghosted patient records (records present in cache, but no longer on the server)
		for g in cache_study_ghosts.all():
			remove_cache_resource(sonador_manager, sessionmaker, CacheStudy, g.uid)


def cache_index_series(sonador_manager: SonadorServerManager, session, uid, commit=True, link=True, **kwargs):
	'''	Add a series to the Sonador cache

		@returns tuple: CacheSeries, ImagingSeries
	'''
	sx = orthancserver_get_series(sonador_manager, uid)
	return CacheSeries.index(session, sx, commit=commit, link=link, **kwargs), sx


def cache_bulk_index_series(sonador_manager: SonadorServerManager, sessionmaker, db_series=None, batch_size=100, limit=None, offset=None,
		**kwargs):
	'''	Bulk import of DICOM series objects into the Sonador Resource Cache
	'''
	opcount = 0
	
	try:	

		with sessionmaker() as session:
			db_series = db_series or session.query(Resource).filter_by(resourcetype=ORTHANCDB_SERIES_TYPE)
			db_series_count = len(paginate_query_results(db_series, offset, limit)) if (limit or offset) \
				else db_series.count()
			
			logger.warning('Begin bulk index of DICOM series data: queue-size="%s"' % db_series_count)

			for r_db in (paginate_query_results(db_series, offset, limit) if (limit or offset) else db_series):

				# Index series, increment batch counter
				cs, sx = cache_index_series(sonador_manager, session, r_db.publicid, commit=False, **kwargs)
				opcount += 1

				# Commit records to database at end of batch
				if opcount % batch_size == 0:
					session.commit()
					logger.warning('Bulk DICOM series data committed to resource cache: progress=%s/%s' % (opcount, db_series_count))

				logger.debug('Import of series (uid=%s) to cache successful: study="%s" modality="%s"'
					% (sx.pk, sx.study, sx.modality))

			session.commit()
			session.flush()

		return OpResults(True, opcount, None)

	except Exception as err:
		logger.error('Unable to complete indexing of the series records. Error:\n%s' % err)
		return OpResults(False, opcount, err)


def cache_syncdb_index_series(sonador_manager: SonadorServerManager, sessionmaker, db_series=None, cache_series_ghosts=None, 
		batch_size=100, limit=1000, offset=0, **kwargs):
	'''	Synchronize Orthanc's resource database with the Sonador Resource cache.

		1. Series records present in the Resources, but not in CacheSeries table are propagated to the cache.
		2. Series records present in CacheSeries, but no longer in Resources are pruned.
	'''
	with sessionmaker() as session:

		# Determine missing studies and ghosted studies
		db_series = db_series or cache_index_missing_resources(session, CacheSeries)
		cache_series_ghosts = cache_series_ghosts or cache_index_ghosts(session, CacheSeries)

		logger.warning('Synchronize DICOM series resources to Sonador resource cache: missing-series="%s" ghosted-series="%s"' % (
			db_series.count(), cache_series_ghosts.count(),
		))

		# Bulk index patients
		if db_series.count():
			cache_bulk_index_series(sonador_manager, sessionmaker, db_series=db_series,
				batch_size=batch_size, limit=limit, offset=offset, **kwargs)

		# Remove ghosted patient records (records present in cache, but no longer on the server)
		for g in cache_series_ghosts.all():
			remove_cache_resource(sonador_manager, sessionmaker, CacheSeries, g.uid)


def cache_index_instance(sonador_manager: SonadorServerManager, session, uid, commit=True, link=True, **kwargs):
	'''	Import a DICOM instance into the Sonador resource cache
	'''
	dcm = orthancserver_get_instance(sonador_manager, uid)
	return CacheInstance.index(session, dcm, commit=commit, link=link, **kwargs), dcm


def cache_bulk_index_instances(sonador_manager: SonadorServerManager, sessionmaker, db_instances=None, batch_size=100, limit=None, offset=None,
		**kwargs):
	'''	Bulk import of DICOM instances into the Sonador Resource Cache
	'''
	opcount = 0

	try:
		
		with sessionmaker() as session:
			db_instances = db_instances or session.query(Resource).filter_by(resourcetype=ORTHANCDB_INSTANCE_TYPE)
			db_instance_count = len(paginate_query_results(db_instances, offset, limit)) if (limit or offset) \
				else db_instances.count()
			
			logger.warning('Begin bulk index of DICOM instance data: queue-size="%s"' % db_instance_count)

			for r_db in (paginate_query_results(db_instances, offset, limit) if (limit or offset) else db_instances):

				# Index instances, increment batch counter
				cs, dcm = cache_index_instance(sonador_manager, session, r_db.publicid, commit=False, **kwargs)
				opcount += 1

				# Commit records to database at end of batch
				if opcount % batch_size == 0:
					
					# Commit data to the database and flush session
					session.commit()
					session.flush()
					
					logger.warning('Bulk DICOM instance data committed to resource cache: progreess="%s/%s' % (opcount, db_instance_count))

				logger.warning('Import of DICOM instance (uid=%s) to cache successful: series="%s"' % (dcm.pk, dcm.series))

			session.commit()
			session.flush()

		return OpResults(True, opcount, None)

	except Exception as err:
		logger.error('Unable to complete indexing of the series records. Error:\n%s' % err)
		return OpResults(False, opcount, err)


def cache_syncdb_index_instances(sonador_manager: SonadorServerManager, sessionmaker, db_instances=None, cache_instances_ghosts=None, 
		batch_size=100, limit=5000, offset=0, **kwargs):
	'''	Synchronize Orthanc's resource database with the Sonador Resource cache.

		1. Instance records present in the Resources, but not in CacheInstance table are propagated to the cache.
		2. Instance records present in CacheInstance, but no longer in Resources are pruned.
	'''
	with sessionmaker() as session:

		# Determine missing instances and ghosted resources
		db_instances = db_instances or cache_index_missing_resources(session, CacheInstance)
		cache_instances_ghosts = cache_instances_ghosts or cache_index_ghosts(session, CacheInstance)

		logger.warning('Synchronize DICOM instance resources to cache: missing-instances="%s" ghosted-instances="%s"' % (
			db_instances.count(), cache_instances_ghosts.count(),
		))

		# Bulk index instances
		if db_instances.count():
			cache_bulk_index_instances(sonador_manager, sessionmaker, db_instances=db_instances, 
				batch_size=batch_size, limit=limit, offset=offset, **kwargs)

		# Remove ghost records (records present in the cache, but no longer on the server)
		for g in cache_instances_ghosts.all():
			remove_cache_resource(sonador_manager, sessionmaker, CacheInstance, g.uid)


def cache_index_serverchange_callback(sonador_manager: SonadorServerManager, sessionmaker, cacheindex_method, **kwargs):
	'''	Create a server change callback that can be registered to an Orthanc event. 
		(Special attention needs to be paid to Index entries created using this method 
		as it is not possible to guarantee referential integrity at the time that the server change
		event executes. A suggested pattern is to create an initial index entry on a "new" signal
		and then link on a "stable" or "update" signal.)

		@input sonador_manager (orthanc_sonador.manager.SonadorServerManager): Sonador manager instance.
		@input sessionmaker (sqlalchemy.orm.session.sessionmaker): Sessionmaker instance to be used
			for database connections.
		@input cacheindex_method (callable): method that should be used in the server change
			event handler.

		@returns callable with signature:
			@input changeType: type of server event
			@input level: DICOM resource level
			@input resource: UID of the modified resource
	'''
	def serverchange_callback(changeType, level, resource):
		with sessionmaker() as session:
			logger.debug('DCM resource event (change-type=%s): level=%s resource=%s' % (changeType, level, resource))
			
			try: cacheindex_method(sonador_manager, session, resource, changeType=changeType, level=level, **kwargs)
			except Exception as err:
				logger.error('Unable to execute DCM resource callback: level=%s resource=%s. Error: "%s".\nTraceback:\n%s'
					% (level, resource, err, traceback.format_exc()))

	return serverchange_callback


def cache_index_logchange(sonador_manager: SonadorServerManager, session, resource, changeType=None, level=None, **kwargs):
	'''	Log changes within Orthanc. This method is used for debugging
	'''
	logger.warning('Orthanc resource event (change-type=%s): level=%s resource=%s. arguments="%s"' % (
		changeType, level, resource, kwargs
	))


def remove_cache_resource(sonador_manager: SonadorServerManager, sessionmaker, cachemodel, resource, commit=True,
		log_previously_deleted=True, check_parents=True, **kwargs):
	'''	Remove a resource instance from the provided cache model

		@input sonador_manager (orthanc_sonador.manager.SonadorServerManager): Sonador manager instance
		@input sessionmaker (sqlalchemy.orm.session.sessionmaker): Sessionmaker instance
			to be used for database connections
		@input cachemodel: cachemodel type which should be used for removing the instance
	'''
	with sessionmaker() as session:


		try :

			# Raise an error if no cachemodel provided
			if cachemodel is None:
				raise Exception('Unable to remove Sonador resource=%s invalid Resource cache model "%s"' % cachemodel.__name__)
		
			# Query cache model instance from the database and attempt to remove
			c = session.query(cachemodel).filter_by(uid=resource).first()
			if c: session.delete(c)
			else:

				# Notify user that the resource was removed previously
				if log_previously_deleted:
					logger.warning('Unable to retrieve resource "%s" from cache table "%s", instance does not exist.'
						% (resource, cachemodel.__tablename__))

			# Remove private tags instance
			pc = session.query(cachemodel.privatetags_resource_model).filter_by(uid=resource).first()
			if pc: session.delete(pc)

			# Remove indexed date/time tags
			for dc in session.query(cachemodel.datetime_resource_model).filter_by(uid=resource):
				session.delete(dc)

			# Remove comments
			if hasattr(cachemodel, 'comment_model'):
				for c in session.query(cachemodel.comment_model).filter_by(**{
						cachemodel.comment_model.resource_foreignkey_attr: resource
					}):
					session.delete(c)

			# Remove access control lists: group and user models
			for acl_model in (getattr(cachemodel, 'group_acl_model', None), getattr(cachemodel, 'user_acl_model', None)):

				if acl_model:
					for acl in session.query(acl_model).filter_by(resource=resource):
						session.delete(acl)

			# Remove worklists
			if hasattr(cachemodel, 'worklist_reviewer_model'):
				for w in session.query(cachemodel.worklist_reviewer_model).filter_by(**{ 'resource': resource }):
					session.delete(w)			

			# Commit changes to database
			if commit:
				session.commit()
				session.flush()

			# Check parent resources after deletion and remove study/series without any siblings.
			# This fixes a bug in the Sonador Resource Cache where studies and patients without any children
			# become orphaned.
			if check_parents and c and isinstance(c, (CacheStudy, CacheSeries)) and getattr(c, 'parent_id', None):

				if session.query(type(c)).filter(c.parent_id == c.parent_id).count() == 0:
					remove_cache_resource(sonador_manager, sessionmaker, c.parent_resource_model, c.parent_id,
						check_parents=check_parents, log_previously_deleted=False)

		except Exception as err:
			logger.error('Unable to remove resource "%s" from cache table "%s". Error:\n%s\nTraceback: %s'
				% (resource, cachemodel.__tablename__, err, traceback.format_exc()))


def remove_cache_serverchange_callback(sonador_manager: SonadorServerManager, sessionmaker, cachemodel, **kwargs):
	'''	Create a server change callback that can be registered to an Orthanc event for removing DICOM 
		resources from the server cache.

		@input sonador_manager (orthanc_sonador.manager.SonadorServerManager): Sonador manager instance.
		@input sessionmaker (sqlalchemy.orm.session.sessionmaker): Sessionmaker instance
			to be used for database connections.
		@input cachemodel: cachemodel type which should be used for removing the instance.

		@returns callable with signature:
			@input changeType: type of server event
			@input level: DICOM resource level
			@input resource: UID of the modified resource
	'''
	def serverchange_callback(changeType, level, resource, commit=True):
		'''	Server change callback method
		'''
		# Ensure that a valid cache model was provided
		if cachemodel is not None:

			logger.debug('remove DICOM resource (change-type=%s): level=%s resource=%s' % (changeType, level, resource))
			return remove_cache_resource(sonador_manager, sessionmaker, cachemodel, resource, commit=commit, **kwargs)

	return serverchange_callback