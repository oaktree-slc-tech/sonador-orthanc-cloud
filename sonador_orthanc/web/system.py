'''	Web views which provide details about the Orthanc instance, active plugins, and configuration.
'''
import json, orthanc, logging, datetime

import client.apisettings as gcapicodes
from client.apisettings import AUTH
from client.errors import ConfigurationError

from sonador_orthanc_common.apisettings import ORTHANC_SONADOR_CONFIG_URL, ORTHANC_SONADOR_VERSION, \
	ORTHANC_CONFIG_HTTP_SERVER_SECURE, ORTHANC_CONFIG_ORTHANC_DATABASE, ORTHANC_CONFIG_ACTIVE_PLUGINS, \
	ORTHANC_CONNECTION_STATE, ORTHANC_CONNECTION_STATE_CONNECTED, ORTHANC_CONNECTION_STATE_OFFLINE, \
	ORTHANC_SONADOR_CONNECTION
from sonador.serialization import SonadorJsonEncoder

from ..apisettings import VERSION, SONADOR_CACHE_COUNT_PATIENT, SONADOR_CACHE_COUNT_STUDY, SONADOR_CACHE_COUNT_SERIES
from ..db.cache import CacheSeries, CacheStudy, CachePatient

from .base import OrthancBaseView

logger = logging.getLogger(__name__)


class SonadorOrthancSystemReportView(OrthancBaseView):
	'''	View instance showing Orthanc and Sonador components	
	'''
	orthanc_conf = None
	servermanager = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		if not self.orthanc_conf:
			raise ConfigurationError('Unable to initialize system view, invalid Orthanc configuration.')
		if not self.servermanager:
			raise ConfigurationError('Unable to initialize system view, invalid Orthanc server manager.')

	def get(self, output, uri, request):
		'''	Retrieve Orthanc system details: plugins, config, security settings, and available DICOM tags.
			Add Sonador specific settings.			
		'''
		# Retrieve active plugins
		sys_info = json.loads(orthanc.RestApiGet('/system'))
		sys_info[ORTHANC_CONFIG_ACTIVE_PLUGINS] = json.loads(orthanc.RestApiGet('/plugins'))
		sys_info[ORTHANC_CONFIG_ORTHANC_DATABASE] = json.loads(orthanc.RestApiGet('/statistics'))

		# Check active plugins, if HttpServer marked as "insecure" and "authorization" plugin enabled,
		# modify the system report to report "secure".
		if sys_info.get(ORTHANC_CONFIG_HTTP_SERVER_SECURE) == False and AUTH in sys_info.get(ORTHANC_CONFIG_ACTIVE_PLUGINS, []):
			sys_info[ORTHANC_CONFIG_HTTP_SERVER_SECURE]= True

		# Sonador/Orthanc Version
		sys_info[ORTHANC_SONADOR_CONFIG_URL] = self.servermanager.server.url
		sys_info[ORTHANC_SONADOR_VERSION] = VERSION

		return self.send_response(json.dumps(sys_info, cls=SonadorJsonEncoder))


class SonadorOrthancSystemStatusView(OrthancBaseView):
	'''	Test current status of the system: Sonador and database connection
	'''
	servermanager = None
	sessionmaker = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		if not self.servermanager:
			raise ConfigurationError('Unable to initialize status view, invalid Orther server manager.')
		if not self.sessionmaker:
			raise ConfigurationError('Unable to initialize status view, invalid session maker instance.')

	def get(self, output, uri, request, *args, **kwargs):
		''' Check database and Sonador server connection status
		'''
		response = kwargs.get('response') or { gcapicodes.OPCODE: ORTHANC_CONNECTION_STATE }

		# Check connection to Sonador
		try:
			iserver = self.servermanager.server.get_imageserver(self.servermanager.imageserver_id)
			response[ORTHANC_SONADOR_CONNECTION] = {
				gcapicodes.OPRESULT: gcapicodes.SUCCESS,
				'ts': datetime.datetime.utcnow(),
				gcapicodes.STATUS: ORTHANC_CONNECTION_STATE_CONNECTED,
			}

		# Notify user that the gateway is offline
		except Exception as err:
			response[ORTHANC_SONADOR_CONNECTION] = {
				gcapicodes.OPRESULT: gcapicodes.FAIL,
				'ts': datetime.datetime.utcnow(),
				gcapicodes.STATUS: ORTHANC_CONNECTION_STATE_OFFLINE,
				gcapicodes.ERROR: 'Unable to connect to Sonador instance "%s" due to an error:\n%s'
					% (self.servermanager.server.url, err),
				ORTHANC_SONADOR_CONFIG_URL: self.servermanager.server.url,
			}

		# Check connection to database
		try:

			# Count number of patients, studies, and series in the Sonador resource cache
			with self.sessionmaker() as session:
				response[ORTHANC_CONFIG_ORTHANC_DATABASE] = {
					gcapicodes.OPRESULT: gcapicodes.SUCCESS,
					'ts': datetime.datetime.utcnow(),
					SONADOR_CACHE_COUNT_PATIENT: session.query(CachePatient).count(),
					SONADOR_CACHE_COUNT_STUDY: session.query(CacheStudy).count(),
					SONADOR_CACHE_COUNT_SERIES: session.query(CacheSeries).count(),
					gcapicodes.STATUS: ORTHANC_CONNECTION_STATE_CONNECTED,
				}

		# Notify user that database is offline
		except Exception as err:
			response[ORTHANC_CONFIG_ORTHANC_DATABASE] = {
				gcapicodes.OPRESULT: gcapicodes.FAIL,
				'ts': datetime.datetime.utcnow(),
				gcapicodes.STATUS: ORTHANC_CONNECTION_STATE_OFFLINE,
				gcapicodes.ERROR: 'Unable to connect to Orthanc database due to an error:\n%a' % err,
			}

		# Set response status code: 200 if all components online, 500 otherwise
		if response.get(ORTHANC_SONADOR_CONNECTION, {}).get(gcapicodes.STATUS) == ORTHANC_CONNECTION_STATE_CONNECTED \
			and response.get(ORTHANC_CONFIG_ORTHANC_DATABASE, {}).get(gcapicodes.STATUS) == ORTHANC_CONNECTION_STATE_CONNECTED:
			status_code = 200
		else: status_code = 500

		return self.send_response(json.dumps(response, cls=SonadorJsonEncoder), status_code=status_code)