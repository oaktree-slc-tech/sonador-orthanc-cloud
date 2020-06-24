import six, os, orthanc, json, logging, pprint, threading, requests, traceback, posixpath
from sonador.helpers import SonadorServer
from sonador.remote import fetch_sonador_dataobject
from sonador.servers import SonadorImagingServer

logger = logging.getLogger(__name__)

TIMER_MINUTE = 60
TIMER_10MIN = TIMER_MINUTE*10
TIMER_30MIN = TIMER_MINUTE*30
TIMER_HOUR = TIMER_MINUTE*60
TIMER_DAILY = TIMER_HOUR*24


# Background timers 
CONFIG_TIMER = None


orthanc.LogWarning('Sonador/Orthanc integration plugin enabled')

# Load configuration and extract API connection parameters
# orthanc_config = orthanc.OrthancPluginGetConfiguration()
CONF = json.loads(orthanc.GetConfiguration())
CONF_SONADOR = CONF.get('Sonador', {})

# Initialize Sonador API client and check that all required authentication
# components are present (Sonador API clients should authenticate with API tokens)
if not CONF_SONADOR:
	raise ValueError('Invalid configuration, unable to locate Sonador section of configuration')

# Connection URL
if not CONF_SONADOR.get('SonadorUrl'):
	raise ValueError('Invalid configuration, invalid Sonador URL')

# Access Credentials
if not CONF_SONADOR.get('ApiToken'):
	if not CONF_SONADOR.get('AccessId') or not CONF_SONADOR.get('SecretKey'):
		raise ValueError('Invalid configuration, missing AccessID or Sonador secret key')

# Orthanc Server ID
if not CONF_SONADOR.get('OrthancServerId'):
	raise ValueError('Invalid configuration, please provide server ID for server instance from Sonador')

# SSL verification
VERIFY_SSL = CONF_SONADOR.get('VerifySSL', False)
INTERNAL_DNS = CONF_SONADOR.get('InternalDns', False)
# 	else False


# Initialize Sonador connection
SONADOR_SERVER = SonadorServer(
	CONF_SONADOR.get('SonadorUrl'), CONF_SONADOR.get('AccessId'), CONF_SONADOR.get('SecretKey'),
	apitoken=CONF_SONADOR.get('ApiToken'), verify=VERIFY_SSL, internal_dns=INTERNAL_DNS)


def sonador_configuration(timer_schedule=TIMER_10MIN):
	'''	Retrieve configuration
	'''
	global CONFIG_TIMER
	CONFIG_TIMER = None

	# Ensure that the DICOMweb plugin is installed
	orthanc.LogInfo('Sync Orthanc configuration from Sonador with local state')
	rcheck = orthanc.RestApiGet('/plugins/dicom-web/')
	dcweb_check = json.loads(rcheck.decode('utf-8') if isinstance(rcheck, six.binary_type) else rcheck)
	orthanc.LogInfo('DICOMweb plugin installed and active:\n%s' % dcweb_check)
	
	try:
		iserver = fetch_sonador_dataobject(
			SONADOR_SERVER, SonadorImagingServer, CONF_SONADOR.get('OrthancServerId'), verify=VERIFY_SSL)
		orthanc.LogInfo(
			'Configure remote DICOM modalities: %s' % ', '.join(
				"%s" % dcm.orthanc_name for dcm in iserver.dicom_modalities))
		
		# Update local server configuration once remote data has
		for dcm in iserver.dicom_modalities:
			orthanc.RestApiPut(posixpath.join('/modalities', dcm.orthanc_name), 
				json.dumps({ 'AET': dcm.aet, 'Port': dcm.port, 'Host': dcm.host }))
		
		# Configuration DICOMweb servers
		orthanc.LogInfo('Configure DICOMweb remotes: %s' % ', '.join(
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
		orthanc.LogError('Unable to update Orthanc configuration from Sonador. Error: %s.\n%s' 
			% (err, traceback.format_exc()))
	finally:
		CONFIG_TIMER = threading.Timer(timer_schedule, sonador_configuration)
		CONFIG_TIMER.start()


def orthanc_sonadorconfig_onchange(changeType, level, resource):
	'''	Manage state changes within Orthanc
	'''
	# Initialize Sonador remote configuration agent
	if changeType == orthanc.ChangeType.ORTHANC_STARTED:
		orthanc.LogWarning('Start Sonador/Orthanc configuration scheduler')
		sonador_configuration()

	elif changeType == orthanc.ChangeType.ORTHANC_STOPPED:
		if CONFIG_TIMER != None:
			orthanc.LogWarning('Stop Sonador/Orthanc configuration scheduler')
			CONFIG_TIMER.cancel()


# Register
orthanc.RegisterOnChangeCallback(orthanc_sonadorconfig_onchange)
