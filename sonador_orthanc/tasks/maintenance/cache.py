'''	Task methods for Orthanc maintenance
'''
import logging, json, posixpath, traceback
from collections import namedtuple
import orthanc

from sonador.servers import SonadorServer
from sonador.imaging.orthanc import ImagingPatient, ImagingStudy, ImagingSeries

from ...db.internal import Resource, \
    ORTHANCDB_PATIENT_TYPE, ORTHANCDB_STUDY_TYPE, ORTHANCDB_SERIES_TYPE
from ...db.cache import CachePatient, CacheStudy, CacheSeries
from ...db.dcmext import CachePatientPrivateTags, CacheStudyPrivateTags, CacheSeriesPrivateTags

from ...manager import SonadorServerManager
from ...helpers import orthancserver_get_patient, orthancserver_get_study, orthancserver_get_series
from ...web.helpers import paginate_query_results

logger = logging.getLogger(__name__)


OpResults = namedtuple('OpResults', ('success', 'err'))


def cache_index_patient(sonador_manager: SonadorServerManager, session, uid, commit=True, **kwargs):
	'''	Add a patient to the Sonador cache

		@returns tuple: CachePatient, ImagingPatient
	'''
	p = orthancserver_get_patient(sonador_manager, uid)
	return CachePatient.index(session, p, commit=commit, **kwargs), p


def cache_bulk_index_patients(sonador_manager: SonadorServerManager, sessionmaker, db_patients=None, batch_size=100, limit=None, offset=None, **kwargs):
	'''	Import a copy of DICOM patient data into the Sonador resource cache
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

		return OpResults(True, None)
	
	except Exception as err:
		logger.error('Unable to complete indexing of patient records. Error:\n%s' % err)
		return OpResults(False, err)


def cache_index_study(sonador_manager: SonadorServerManager, session, uid, commit=True, link=True, **kwargs):
	'''	Add a study to the Sonador cache

		@returns tuple: CacheStudy, ImagingStudy
	'''
	s = orthancserver_get_study(sonador_manager, uid)
	return CacheStudy.index(session, s, commit=commit, link=link, **kwargs), s


def cache_bulk_index_studies(sonador_manager: SonadorServerManager, sessionmaker, db_studies=None, batch_size=100, limit=None, offset=None, **kwargs):
	'''	Import a copy of DICOM study data into the Sonador resource cache

		@returns bool: True if the operation completes successfully, False if there was an error
	'''
	try:
		opcount = 0

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

		return OpResults(True, None)

	except Exception as err:
		logger.error('Unable to complete indexing of study records. Error:\n%s' % err)
		return OpResults(False, err)


def cache_index_series(sonador_manager: SonadorServerManager, session, uid, commit=True, link=True, **kwargs):
	'''	Add a series to the Sonador cache

		@returns tuple: CacheSeries, ImagingSeries
	'''
	sx = orthancserver_get_series(sonador_manager, uid)
	return CacheSeries.index(session, sx, commit=commit, link=link, **kwargs), sx


def cache_bulk_index_series(sonador_manager: SonadorServerManager, sessionmaker, db_series=None, batch_size=100, limit=None, offset=None,
		**kwargs):
	'''	Import a copy of DICOM series data into the Sonador resource cache
	'''
	try:
		opcount = 0

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

		return OpResults(True, None)

	except Exception as err:
		logger.error('Unable to complete indexing of the series records. Error:\n%s' % err)
		return OpResults(False, None)


def cache_index_serverchange_callback(sonador_manager: SonadorServerManager, sessionmaker, cacheindex_method, link=True, **kwargs):
	'''	Create a server change callback that can be registered to an Orthanc event for indexinig DICOM
		resources. (Special attention needs to be paid to Index entries created using this method 
		as it is not possible to guarantee referential integrity at the time that the server change
		event executes. A suggested pattern is to create an initial index entry on a "new" signal
		and then link on a "stable" or "update" signal.)

		@input sonador_manager (orthanc_sonador.manager.SonadorServerManager): Sonador manager instance.
		@input sessionmaker (sqlalchemy.orm.session.sessionmaker): Sessionmaker instance to be used
			for database connections.
		@input cacheindex_method (callable): indexing method that should be used in the server change
			event handler.

		@returns callable with signature:
			@input changeType: type of server event
			@input level: DICOM resource level
			@input resource: UID of the modified resource
	'''
	def serverchange_callback(changeType, level, resource):
		with sessionmaker() as session:
			logger.debug('index DICOM resource (change-type=%s): level=%s resource=%s' % (changeType, level, resource))
			try: cacheindex_method(sonador_manager, session, resource, link=link, **kwargs)
			except Exception as err:
				logger.error('Unable to index DICOM resource level=%s resource=%s. Error: "%s".\nTraceback:\n%s'
					% (level, resource, err, traceback.format_exc()))

	return serverchange_callback


def remove_cache_resource(sonador_manager: SonadorServerManager, sessionmaker, cachemodel, resource, commit=True):
	'''	Remove a resource instance from the provided cache model

		@input sonador_manager (orthanc_sonador.manager.SonadorServerManager): Sonador manager instance
		@input sessionmaker (sqlalchemy.orm.session.sessionmaker): Sessionmaker instance
			to be used for database connections
		@input cachemodel: cachemodel type which should be used for removing the instance
	'''
	with sessionmaker() as session:

		try :
			# Query cache model instance from the database and attempt to remove
			c = session.query(cachemodel).filter_by(uid=resource).first()
			if c: session.delete(c)
			else:
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

			# Commit changes to database
			if commit:
				session.commit()

		except Exception as err:
			logger.error('Unable to remove resource "%s" from cache table "%s". Error:\n%s'
				% (resource, cachemodel.__tablename__, err))


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
		logger.debug('remove DICOM resource (change-type=%s): level=%s resource=%s' % (changeType, level, resource))
		return remove_cache_resource(sonador_manager, sessionmaker, cachemodel, resource, commit=commit)

	return serverchange_callback