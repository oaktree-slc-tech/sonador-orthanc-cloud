import six, os, orthanc, json, logging, pprint, threading, requests, traceback, posixpath

from sonador.helpers import SonadorServer
from sonador.remote import fetch_sonador_dataobject
from sonador.servers import SonadorImagingServer

from confluent_kafka import Producer

logger = logging.getLogger(__name__)

TIMER_30S = 30
TIMER_MINUTE = 60
TIMER_10MIN = TIMER_MINUTE*10
TIMER_30MIN = TIMER_MINUTE*30
TIMER_HOUR = TIMER_MINUTE*60
TIMER_DAILY = TIMER_HOUR*24

KAFKA_TIMEOUT_DEFAULT = 10


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
ORTHANC_SONADOR_SERVERID = CONF_SONADOR.get('OrthancServerId')
if not ORTHANC_SONADOR_SERVERID:
	raise ValueError('Invalid configuration, please provide server ID for server instance from Sonador')

# SSL verification
VERIFY_SSL = CONF_SONADOR.get('VerifySSL', False)
INTERNAL_DNS = CONF_SONADOR.get('InternalDns', False)

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

SONADOR_SERVER = SonadorServer(
	CONF_SONADOR.get('SonadorUrl'), CONF_SONADOR.get('AccessId'), CONF_SONADOR.get('SecretKey'),
	apitoken=CONF_SONADOR.get('ApiToken'), verify=VERIFY_SSL, internal_dns=INTERNAL_DNS)


def sonador_configuration(timer_schedule=TIMER_10MIN):
	'''	Retrieve configuration data from Sonador and update local cache
	'''
	global CONFIG_TIMER
	CONFIG_TIMER = None

	# Ensure that the DICOMweb plugin is installed
	orthanc.LogInfo('Sync Orthanc configuration from Sonador with local state')
	rcheck = orthanc.RestApiGet('/plugins/dicom-web/')
	dcweb_check = json.loads(rcheck.decode('utf-8') if isinstance(rcheck, six.binary_type) else rcheck)
	orthanc.LogInfo('DICOMweb plugin installed and active:\n%s' % dcweb_check)
	
	try:
		iserver = SONADOR_SERVER.get_imageserver(ORTHANC_SONADOR_SERVERID)
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
		'OrthancServerId': ORTHANC_SONADOR_SERVERID,
		'Resource': 'Instance',
		'ID': instanceId, 
		'Source': 'DCM' if dicom.GetInstanceOrigin() == orthanc.InstanceOrigin.DICOM_PROTOCOL \
			else 'REST' if dicom.GetInstanceOrigin() == orthanc.InstanceOrigin.REST_API \
			else None,
		'DCM': idata,
	}

	# Move patient, study, and series identifiers to the top-level
	if idata.get('PatientID'):
		mdata['PatientID'] = idata.get('PatientID')
	if idata.get('StudyID'):
		mdata['StudyID'] = idata.get('StudyID')
	if idata.get('SeriesInstanceUID'):
		mdata['SeriesInstanceUID'] = idata.get('SeriesInstanceUID')
	
	orthanc.LogInfo('JSON data for DICOM instance:\n%r' % mdata)

	# Send to Kafka
	KAFKA_PRODUCER.produce(KAFKA_TOPIC, json.dumps(mdata), callback=orthanc_kafka_delivery_report)


def kafka_message_flush(timer_schedule=TIMER_30S, poll_timeout=KAFKA_TIMEOUT_DEFAULT):
	'''	Flush messages to Kafka and retrieve transaction receipts
	'''
	global KAFKA_TIMER
	KAFKA_TIMER = None

	try:
		orthanc.LogInfo('Push Kafka messages to broker: %s' % KAFKA_SERVERS)
		KAFKA_PRODUCER.poll(0)
	
	except Exception as err:
		orthanc.LogError('Unable to perform scheduled Kafka message flush due. Error: %s.\n%s'
			% (err, traceback.format_exc()))
	
	finally:
		KAFKA_TIMER = threading.Timer(poll_timeout, kafka_message_flush)
		KAFKA_TIMER.start()



# Plugin Initialization Event Handlers

def orthanc_kafka_onchange(changeType, level, resource):
	'''	Initialize Orthanc/Kafka integration. Handle server state changes.

		@event: Initialize "poll" event loop to retrieve Kafka message receipts
			and flush messages to the Kafka broker.
		@event: Perform one final "flush" to allow for messages to be delieverd 
			and report callbacks to be triggered.
	'''

	# Initialize Sonador Kafka agent
	if changeType == orthanc.ChangeType.ORTHANC_STARTED:
		orthanc.LogWarning('Start Orthanc/Kafka message scheduler')
		kafka_message_flush()

	# Stop Orthanc Kafka agent
	elif changeType == orthanc.ChangeType.ORTHANC_STOPPED:
		
		# Cancel background scheduler
		if KAFKA_TIMER != None:
			orthanc.LogWarning('Stop Orthanc/Kafka message scheduler')
			KAFKA_TIMER.cancel()

		# Flush all pending messages
		KAFKA_PRODUCER.flush()


def orthanc_sonador_onchange(changeType, level, resource):
	'''	Initialize Orthanc/Sonador inegration. Handle server state changes.

		@event startup: Initialize the server configuration and background timers.
		@event shutdown: Stop all background timers
	'''
	# Initialize Sonador remote configuration agent
	if changeType == orthanc.ChangeType.ORTHANC_STARTED:
		orthanc.LogWarning('Start Sonador/Orthanc configuration scheduler')
		sonador_configuration()

		if KAFKA_PRODUCER != None:
			orthanc_kafka_onchange(changeType, level, resource)

	# Stop Orthanc remote configuration agent
	elif changeType == orthanc.ChangeType.ORTHANC_STOPPED:
		if CONFIG_TIMER != None:
			orthanc.LogWarning('Stop Sonador/Orthanc configuration scheduler')
			CONFIG_TIMER.cancel()


def orthanc_sonador_onstoredinstance(dicom, instanceId):
	'''	General on stored instance event handler. Manages state changes for
		DICOM instances when they are committed to the backend storage.
	'''
	if KAFKA_PRODUCER != None:
		orthanc_kafka_export_instance_meta(dicom, instanceId)


# Register callbacks
orthanc.RegisterOnChangeCallback(orthanc_sonador_onchange)
orthanc.RegisterOnStoredInstanceCallback(orthanc_sonador_onstoredinstance)
