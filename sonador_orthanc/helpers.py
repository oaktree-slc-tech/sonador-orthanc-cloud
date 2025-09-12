import six, os, subprocess, datetime, json, logging, copy, requests, traceback, posixpath
from urllib.parse import quote_plus as urlquote

from client.utils.conversion import str2bool
from client.errors import ConfigurationError

from sonador.apisettings import \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES
from sonador.servers import SonadorServer
from sonador.servers.base import OrthancServerBase
from sonador.imaging.orthanc import ImagingPatient, ImagingStudy, ImagingSeries

from sonador_orthanc_common.apisettings import ORTHANC_SERVER_ID
from sonador_orthanc_common.helpers import init_sonador_server, \
	orthancserver_get_patient, orthancserver_get_study, orthancserver_get_series, orthancserver_get_instance, \
	orthancserver_sync_modalities

from .apisettings import ORTHANC_MAINDICOM_TAGS_DEFAULT, ORTHANC_DEFAULT_ENCODING, \
	ORTHANC_CONFIG_SECTION_DICOMWEB, ORTHANC_CONFIG_SECTION_EXTRADICOMTAGS
from .manager import SonadorServerManager

logger = logging.getLogger(__name__)


def init_postgresdb_connstr(postgres_config: dict, connection_template='postgresql+psycopg2://%s:%s@%s:%s/%s'):
	'''	Create PostgreSQL connection string for SQL alchemy from the PostgreSQL section of the Orthanc config.
	'''
	postgres_host = postgres_config.get('Host')
	postgres_port = postgres_config.get('Port')
	postgres_database = postgres_config.get('Database')
	postgres_username = postgres_config.get('Username')
	postgres_password = postgres_config.get('Password')

	if not postgres_host or not postgres_port or not postgres_database or not postgres_username or not postgres_password:
		raise ConfigurationError(
			'Invalid Orthanc PostgreSQL configuration. Missing host, port, database, username, or password.')

	return connection_template % (
		postgres_username, urlquote(postgres_password), postgres_host, postgres_port, postgres_database,
	)


def init_postgresdb_conn(postgres_config: dict, pool_pre_ping=True, 
		pool_size=30, max_overflow=50, pool_timeout=60, pool_recycle=1800, **kwargs):
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
	sql_connstr = init_postgresdb_connstr(postgres_config, **kwargs)
	pool_size = int(postgres_config.get('SonadorPoolSize', pool_size))
	max_overflow = int(postgres_config.get('SonadorMaxPoolOverflow', max_overflow))
	pool_timeout = int(postgres_config.get('SonadorPoolTimeout', pool_timeout))
	pool_recycle = int(postgres_config.get('SonadorPoolRecycle', pool_recycle))

	# Initialize SQL engine instance and OrthancSession class
	orthanc_sqlengine = sql_create_engine(sql_connstr, pool_pre_ping=pool_pre_ping, pool_recycle=pool_recycle,
		pool_size=pool_size, max_overflow=max_overflow, pool_timeout=pool_timeout)
	OrthancSession = sessionmaker(bind=orthanc_sqlengine)

	return orthanc_sqlengine, OrthancSession


def orthanc_maindicom_tags(orthanc_conf, maindicom_tags_default=ORTHANC_MAINDICOM_TAGS_DEFAULT, dcm_privatetags=None):
	'''	Retrieve the main DICOM tags list for the server
	'''
	# Default main DICOM tags. Refer to https://book.orthanc-server.com/faq/main-dicom-tags.html.
	cdicomtags = copy.copy(maindicom_tags_default)
	dcm_privatetags = dcm_privatetags or {}

	# Create unified list of tags
	if not cdicomtags.get('Tags'):
		cdicomtags['Tags'] = set()

	# Extra main DICOM tags defined in configuration
	for rtype in (IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES):
		ctagset = cdicomtags.get(rtype, set())
		ctagset_private = dcm_privatetags.get(rtype, set())

		# Add extra DICOM tags for resource type to tag set
		extratags = orthanc_conf.get(ORTHANC_CONFIG_SECTION_EXTRADICOMTAGS, {}).get(rtype, [])
		logger.debug('Extra DICOM tags (resource=%s): %s' % (rtype, ', '.join(extratags)))
		ctagset.update(extratags)
		if ctagset_private:
			ctagset.update(ctagset_private)
		
		cdicomtags[rtype] = ctagset
		cdicomtags['Tags'].add(t for t in ctagset)

	return cdicomtags


def orthancserver_sync_dcmweb_remotes(orthanc_conf, sonador_manager: SonadorServerManager, iserver: OrthancServerBase,
		orthanc_default_encoding=ORTHANC_DEFAULT_ENCODING):
	'''	Retrieve DICOMweb remotes which should be associated with the Orthanc server and sync against
		those registered locally.
	'''
	dicomweb_conf = orthanc_conf.get(ORTHANC_CONFIG_SECTION_DICOMWEB, {})
	dicomweb_plugin_root = dicomweb_conf.get('Root')
	dicomweb_root = dicomweb_conf.get('SonadorDicomWebRoot') or dicomweb_plugin_root

	# Retrieve DICOMweb remote list from Sonador
	logger.info('Configure DICOMweb remotes: %s' % ', '.join(
				"%s" % dcmweb.orthanc_name for dcmweb in iserver.dicomweb_remotes))
	iserver_local = sonador_manager.get_internal_imageserver()
	
	# Create DICOMweb remote entry on imaging server instance
	if dicomweb_conf.get('Enable'):

		for dcmweb in iserver.dicomweb_remotes:
			rurl = iserver_local.orthanc_apiurl(posixpath.join(dicomweb_plugin_root, 'servers', dcmweb.orthanc_name))
			r = iserver_local._request_put(rurl, 'Unable to update DICOM-web instance="%s"' % dcmweb.orthanc_name, json={
				'Url': dcmweb.dicomweb_url, 'Username': dcmweb.username, 'Password': dcmweb.password,
			})

			if not r.ok:
				raise ValueError('Unable to update DICOMweb configuration. Status code: %s. Request content:\n%s'
					% (r.status_code ,r.content.decode('utf-8')))


def init_fetch_sonador_configuration_callback(orthanc_conf, sonador_servermanager: SonadorServerManager, 
		orthanc_default_encoding=ORTHANC_DEFAULT_ENCODING):
	'''	Initialize Sonador/Orthanc integration callback function. The integration callback
		is a scheduled task that fetches the Orthanc configuration and updates the local Orthanc configuration.
	'''
	import orthanc

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
			orthancserver_sync_modalities(sonador_servermanager, iserver, orthanc_default_encoding=orthanc_default_encoding)
			orthancserver_sync_dcmweb_remotes(orthanc_conf, sonador_servermanager, iserver, 
				orthanc_default_encoding=orthanc_default_encoding)

		except Exception as err:
			logger.error(
				'Unable to update Orthanc configuration from Sonador. Error: %s.\n%s'  % (err, traceback.format_exc()))

	return fetch_sonador_configuration
