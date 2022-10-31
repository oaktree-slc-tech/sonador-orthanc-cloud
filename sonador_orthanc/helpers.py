import six, os, subprocess, datetime, json, logging, copy, requests, traceback, posixpath
from urllib.parse import quote_plus as urlquote

import orthanc

from client.utils.conversion import str2bool
from client.errors import ConfigurationError

from sonador.apisettings import \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES
from sonador.servers import SonadorServer
from sonador.imaging.orthanc import ImagingPatient, ImagingStudy, ImagingSeries

from sonador_orthanc_common.apisettings import ORTHANC_SERVER_ID
from sonador_orthanc_common.helpers import init_sonador_server, \
	orthancserver_get_patient, orthancserver_get_study, orthancserver_get_series, \
	orthancserver_sync_modalities, orthancserver_sync_dcmweb_remotes

from .apisettings import ORTHANC_MAINDICOM_TAGS_DEFAULT, ORTHANC_CONFIG_SECTION_EXTRADICOMTAGS, \
	ORTHANC_DEFAULT_ENCODING
from .manager import SonadorServerManager

logger = logging.getLogger(__name__)


def init_postgresdb_conn(postgres_config: dict, connection_template='postgresql+psycopg2://%s:%s@%s:%s/%s',
		pool_pre_ping=True, pool_recycle=300):
	'''	 Initialize SQLalchemy engine and session maker
	'''
	from sqlalchemy import create_engine as sql_create_engine, MetaData as SqlMetaData
	from sqlalchemy.orm import sessionmaker
	from sqlalchemy import inspect as dbinspect
	from sqlalchemy import func as sqlfunc

	from .db.base import DbBase, AutoDbBase
	from .db.cache import CachePatient, CacheStudy, CacheSeries
	from .db.internal import Resource

	# Create database connection string
	postgres_host = postgres_config.get('Host')
	postgres_port = postgres_config.get('Port')
	postgres_database = postgres_config.get('Database')
	postgres_username = postgres_config.get('Username')
	postgres_password = postgres_config.get('Password')

	if not postgres_host or not postgres_port or not postgres_database or not postgres_username or not postgres_password:
		raise ConfigurationError(
			'Invalid Orthanc PostgreSQL configuration. Missing host, port, database, username, or password.')

	sql_connstr = connection_template % (
		postgres_username, urlquote(postgres_password), postgres_host, postgres_port, postgres_database,
	)

	# Initialize SQL engine instance and OrthancSession class
	orthanc_sqlengine = sql_create_engine(sql_connstr, pool_pre_ping=pool_pre_ping, pool_recycle=pool_recycle)
	OrthancSession = sessionmaker(bind=orthanc_sqlengine)

	return orthanc_sqlengine, OrthancSession


def orthanc_maindicom_tags(orthanc_conf, maindicom_tags_default=ORTHANC_MAINDICOM_TAGS_DEFAULT):
	'''	Retrieve the main DICOM tags list for the server
	'''
	# Default main DICOM tags. Refer to https://book.orthanc-server.com/faq/main-dicom-tags.html.
	cdicomtags = copy.copy(maindicom_tags_default)
	
	# Extra main DICOM tags defined in configuration
	for rtype in (IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES):
		ctagset = cdicomtags.get(rtype, set())

		# Add extra DICOM tags for resource type to tag set
		extratags = orthanc_conf.get(ORTHANC_CONFIG_SECTION_EXTRADICOMTAGS, {}).get(rtype, [])
		logger.debug('Extra DICOM tags (resource=%s): %s' % (rtype, ', '.join(extratags)))
		ctagset.update(extratags)
		
		cdicomtags[rtype] = ctagset

	return cdicomtags


def init_fetch_sonador_configuration_callback(sonador_servermanager: SonadorServerManager, 
		orthanc_default_encoding=ORTHANC_DEFAULT_ENCODING):
	'''	Initialize Sonador/Orthanc integration callback function. The integration callback
		is a scheduled task that fetches the Orthanc configuration and updates the local Orthanc configuration.
	'''
	def fetch_sonador_configuration():
		'''	Retrieve configuration data from Sonador and update local cache
		'''
		# Ensure that the DICOMweb plugin is installed
		logger.info('Sync Orthanc configuration from Sonador with local state')
		rcheck = orthanc.RestApiGet('/plugins/dicom-web/')
		dcweb_check = json.loads(rcheck.decode(orthanc_default_encoding) if isinstance(rcheck, six.binary_type) else rcheck)
		logger.info('DICOMweb plugin installed and active:\n%s' % dcweb_check)
		
		try:
			iserver = sonador_servermanager.server.get_imageserver(sonador_servermanager.imageserver_id)

			# Apply DICOM and DICOMweb configuration from Sonador
			orthancserver_sync_modalities(iserver, orthanc_default_encoding=orthanc_default_encoding)
			orthancserver_sync_dcmweb_remotes(iserver, orthanc_default_encoding=orthanc_default_encoding)

		except Exception as err:
			logger.error(
				'Unable to update Orthanc configuration from Sonador. Error: %s.\n%s'  % (err, traceback.format_exc()))

	return fetch_sonador_configuration
