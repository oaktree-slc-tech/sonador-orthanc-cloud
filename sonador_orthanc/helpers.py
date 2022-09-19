import six, os, subprocess, datetime, json, logging, copy, requests, traceback, posixpath
from urllib.parse import quote_plus as urlquote

import orthanc

from client.utils.conversion import str2bool
from client.errors import ConfigurationError

from sonador.apisettings import \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES
from sonador.servers import SonadorServer
from sonador.imaging.orthanc import ImagingPatient, ImagingStudy, ImagingSeries

from .apisettings import ORTHANC_MAINDICOM_TAGS_DEFAULT, ORTHANC_CONFIG_SECTION_EXTRADICOMTAGS, \
	ORTHANC_DEFAULT_ENCODING
from .manager import SonadorServerManager

logger = logging.getLogger(__name__)



def init_sonador_server(sonador_config: dict):
	'''	Initialize Sonador server
	'''
	# Connection URL
	if not sonador_config.get('SonadorUrl'):
		raise ValueError('Invalid configuration, invalid Sonador URL')

	# Access Credentials
	if not sonador_config.get('ApiToken'):
		if not sonador_config.get('AccessId') or not sonador_config.get('SecretKey'):
			raise ValueError('Invalid configuration, missing AccessID or Sonador secret key')

	# Orthanc Server ID
	ORTHANC_SONADOR_SERVERID = sonador_config.get('OrthancServerId')
	if not ORTHANC_SONADOR_SERVERID:
		raise ValueError('Invalid configuration, please provide server ID for server instance from Sonador')

	# SSL verification
	verify_ssl = sonador_config.get('VerifySSL', False)
	internal_dns = sonador_config.get('InternalDns', False)

	SONADOR_SERVER = SonadorServer(
		sonador_config.get('SonadorUrl'), sonador_config.get('AccessId'), sonador_config.get('SecretKey'),
		apitoken=sonador_config.get('ApiToken'), verify=verify_ssl, internal_dns=internal_dns)

	return SONADOR_SERVER, ORTHANC_SONADOR_SERVERID


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
			logger.info(
				'Configure remote DICOM modalities: %s' % ', '.join(
					"%s" % dcm.orthanc_name for dcm in iserver.dicom_modalities))

			# Retrieve local modality list to compare with remote list
			rmodlist = orthanc.RestApiGet('/modalities')
			orthanc_local_modlist = set(json.loads(
				rmodlist.decode(orthanc_default_encoding) if isinstance(rmodlist, six.binary_type) else rmodlist))

			# Remove modalities that are no longer active
			for dmid in orthanc_local_modlist.difference(set(dcm.orthanc_name for dcm in iserver.dicom_modalities)):
				logger.info('Modality %s no longer active, remove from server.' % dmid)
				orthanc.RestApiDelete(posixpath.join('/modalities', dmid))
			
			# Update local server configuration once remote data has
			for dcm in iserver.dicom_modalities:
				orthanc.RestApiPut(posixpath.join('/modalities', dcm.orthanc_name), 
					json.dumps({
						'AET': dcm.aet, 'Port': dcm.port, 'Host': dcm.host,
						'AllowEcho': dcm.acl_allow_echo, 'AllowFind': dcm.acl_allow_find,
						'AllowGet': dcm.acl_allow_get, 'AllowMove': dcm.acl_allow_move, 'AllowStore': dcm.acl_allow_store
					}))
			
			# Configure DICOMweb servers
			logger.info('Configure DICOMweb remotes: %s' % ', '.join(
				"%s" % dcmweb.orthanc_name for dcmweb in iserver.dicomweb_remotes))
			for dcmweb in iserver.dicomweb_remotes:
				rurl = iserver.orthanc_apiurl(posixpath.join('/dicom-web', 'servers', dcmweb.orthanc_name))
				r = requests.put(rurl, json={
					'Url': dcmweb.dicomweb_url, 'Username': dcmweb.username, 'Password': dcmweb.password,
				}, headers=iserver.orthanc_request_headers())

				if not r.ok:
					raise ValueError('Unable to update DICOMweb configuration. Status code: %s. Request content:\n%s'
						% (r.status_code ,r.content.decode('utf-8')))

		except Exception as err:
			logger.error(
				'Unable to update Orthanc configuration from Sonador. Error: %s.\n%s'  % (err, traceback.format_exc()))

	return fetch_sonador_configuration


def orthancserver_get_patient(sonador_conn: SonadorServer, uid):
	''' Retrieve patient data for the specified UID using the local Orthanc 
		(rather than the REST) interface.

		@input sonador_conn (sonador.servers.SonadorServer): Sonador server instance
		@input uid (str): UID for the patient

		@returns ImagingPatient instance
	'''
	# Retrieve patient and patient metadata
	p = ImagingPatient(sonador_conn, json.loads(orthanc.RestApiGet('/patients/%s' % uid)))
	setattr(p, '_meta', json.loads(orthanc.RestApiGet('/patients/%s/metadata?expand=true' % uid)))
	return p


def orthancserver_get_study(sonador_conn: SonadorServer, uid):
	'''	Retrieve study data for the specified UID using the local Orthanc
		(rather than the REST) interface.

		@input sonador_conn (sonador.servers.SonadorServer): Sonador server instance
		@input uid (str): UID for the study

		@returns ImagingStudy instance
	'''
	# Retrieve study and study metadata
	s = ImagingStudy(sonador_conn, json.loads(orthanc.RestApiGet('/studies/%s' % uid)))
	setattr(s, '_meta', json.loads(orthanc.RestApiGet('/studies/%s/metadata?expand=true' % uid)))
	
	# Retrieve series details for the study
	sx_collection = s.series_from_json(
		json.loads(orthanc.RestApiGet('/studies/%s/series' % s.pk)))
	setattr(s, '_series', sx_collection)

	return s


def orthancserver_get_series(sonador_conn: SonadorServer, uid):
	'''	Retrieve series data for the specified UID using the local Orthanc
		(rather than the REST) interface.

		@input sonador_conn (sonador.servers.SonadorServer): Sonador server instance
		@input uid (str): UID for the study

		@returns ImagingSeries instance
	'''
	# Retrieve series and series metadata
	sx = ImagingSeries(sonador_conn, json.loads(orthanc.RestApiGet('/series/%s' % uid)))
	setattr(sx, '_meta', json.loads(orthanc.RestApiGet('/series/%s/metadata?expand=true' % uid)))
	return sx