import logging, traceback, posixpath
from confluent_kafka import Producer
import orthanc

from ..apisettings import KAFKA_TIMEOUT_DEFAULT, ORTHANC_CONFIG_SECTION_SONADOR, SONADOR_CONF_KAFKA, \
	SONADOR_CONF_KAFKA_SERVERS, SONADOR_CONF_KAFKA_TOPIC, SONADOR_KAFKA_BOOTSTRAP, \
	SONADOR_KAFKA_OPCODE_PUSH_PATIENT, SONADOR_KAFKA_OPCODE_PUSH_STUDY, SONADOR_KAFKA_OPCODE_PUSH_SERIES, \
	SONADOR_KAFKA_OPCODE_PUSH_IMAGE
from ..manager import TIMER_30S, TIMER_MINUTE, TIMER_10MIN, TIMER_30MIN, TIMER_HOUR, TIMER_DAILY

from ..db import cache as sonador_cachedb
from ..db import internal as sonador_internaldb

from .base import SonadorProducer
from . import helpers as kafka_helpers
from . import resource as kafka_resource

logger = logging.getLogger(__name__)


def init_kafka_producer(orthanc_config):
	'''	Initialize Kafka producer
	'''
	conf_sonador = orthanc_config.get(ORTHANC_CONFIG_SECTION_SONADOR, {})
	conf_kafka = conf_sonador.get(SONADOR_CONF_KAFKA)

	producer = SonadorProducer(conf_kafka)
	return producer


def init(orthanc_config, sonador_manager, sessionmaker):
	'''	Initialize Kafka callbacks and message forwarding
	'''
	conf_sonador = orthanc_config.get(ORTHANC_CONFIG_SECTION_SONADOR, {})
	conf_kafka = conf_sonador.get(SONADOR_CONF_KAFKA)

	if getattr(sonador_manager, 'kafka_producer', None):

		# Retrieve server list from producer instance
		kafka_servers = kafka_helpers.get_kafka_servers(conf_kafka)
		if not kafka_servers:
			raise ValueError('Unable to initialize Kafka connection, invalid server list')

		# Retrieve Kafka topic from configuration
		kafka_topic = conf_kafka.get(SONADOR_CONF_KAFKA_TOPIC)
		if not kafka_topic:
			raise ValueError('Unable to initialize Kafka connection, invalid topic')

		
		def orthanc_kafka_delivery_report(err, msg):
			'''	The Kafka producer delivers data asyncrhnously. This function is the
				callback by the Kafka client to indicate whether a message was delivered
				successfully or with an error. For successful deliveries, "err" will be None.

				@input err (exception, None for successful deliveries): Error report from the Kafka
					Producer client.
				@input msg (message instance): Message instance delivered to the Kafka broker
			'''
			if err is not None:
				orthanc.LogError('Unable to deliver message to Kafka instance %s. Error: %s\n%s' % (
					err, kafka_servers, msg.value()
				))
				sonador_manager.kafka_producer.produce(kafka_topic, msg.value(), callback=orthanc_kafka_delivery_report)

		def kafka_message_flush(poll_timeout=KAFKA_TIMEOUT_DEFAULT):
			'''	Flush messages to Kafka and retrieve transaction receipts
			'''
			try:
				logger.info('Push Kafka messages to broker: %s' % kafka_servers)
				sonador_manager.kafka_producer.poll(poll_timeout)

			except Exception as err:
				logger.error('Unable to perform scheduled Kafka message flush due. Error: %s.\n%s'
					% (err, traceback.format_exc()))


		def orthanc_kafka_onstart(changeType, level, resource):
			'''	Initialize Orthanc/Kafka integration. Handle server state changes.

				@event: Initialize "poll" event loop to retrieve Kafka message receipts
					and flush messages to the Kafka broker.
				@event: Perform one final "flush" to allow for messages to be delieverd
					and report callbacks to be triggered.
			'''
			# Initialize Sonador Kafka agent
			orthanc.LogWarning('Start Orthanc/Kafka message scheduler')
			kafka_message_flush()


		def orthanc_kafka_onstop(changeType, level, resource):
			''' Turn off Orthanc/Kafka message scheduler and clear any pending messages
			'''
			# Stop Orthanc Kafka agent and flush all pending messages
			orthanc.LogWarning('Stop Orthanc/Kafka message scheduler')
			sonador_manager.kafka_producer.flush()

		# Kafka start/stop callbacks
		sonador_manager.register_serverchange_callback(
			orthanc.ChangeType.ORTHANC_STARTED, orthanc_kafka_onstart)
		sonador_manager.register_serverchange_callback(
			orthanc.ChangeType.ORTHANC_STOPPED, orthanc_kafka_onstop)

		# Kafka scheduled events
		sonador_manager.register_recurring_task(TIMER_30S, kafka_message_flush)


		# Initialize export of resource data
		kafka_resource.init_export_resource_data(orthanc_config, sonador_manager)
		kafka_resource.init_export_dcm(orthanc_config, sonador_manager)

		# Initialize Kafka push endpoints
		from .web import OrthancKafkaExportView

		# URLs for patient, study, series, and instance Kafka push views
		KAFKA_PATIENT_PUSH_URL = posixpath.join('/patients', r'([0-9a-fA-F]{8}\-?){5}/kafka')
		KAFKA_STUDY_PUSH_URL = posixpath.join('/studies', r'([0-9a-fA-F]{8}\-?){5}/kafka')
		KAFKA_SERIES_PUSH_URL = posixpath.join('/series', r'([0-9a-fA-F]{8}\-?){5}/kafka')
		KAFKA_INSTANCES_PUSH_URL = posixpath.join('/instances', r'([0-9a-fA-F]{8}\-?){5}/kafka')

		orthanc.LogWarning('Enable Sonador/Orthanc patient Kafka push view: %s' % KAFKA_PATIENT_PUSH_URL)
		orthanc.LogWarning('Enable Sonador/Orthanc study Kafka push view: %s' % KAFKA_STUDY_PUSH_URL)
		orthanc.LogWarning('Enable Sonador/Orthanc series Kafka push view: %s' % KAFKA_SERIES_PUSH_URL)
		orthanc.LogWarning('Enable Sonador/Orthanc instances Kafka push view: %s' % KAFKA_INSTANCES_PUSH_URL)

		orthanc.RegisterRestCallback(KAFKA_PATIENT_PUSH_URL, OrthancKafkaExportView.as_view(
			sonador_manager=sonador_manager, sessionmaker=sessionmaker, resource_cachemodel=sonador_cachedb.CachePatient,
			kafka_topic=kafka_topic, kafka_opcode=SONADOR_KAFKA_OPCODE_PUSH_PATIENT))
		orthanc.RegisterRestCallback(KAFKA_STUDY_PUSH_URL, OrthancKafkaExportView.as_view(
			sonador_manager=sonador_manager, sessionmaker=sessionmaker, resource_cachemodel=sonador_cachedb.CacheStudy,
			kafka_topic=kafka_topic, kafka_opcode=SONADOR_KAFKA_OPCODE_PUSH_STUDY))
		orthanc.RegisterRestCallback(KAFKA_SERIES_PUSH_URL, OrthancKafkaExportView.as_view(
			sonador_manager=sonador_manager, sessionmaker=sessionmaker, resource_cachemodel=sonador_cachedb.CacheSeries,
			kafka_topic=kafka_topic, kafka_opcode=SONADOR_KAFKA_OPCODE_PUSH_SERIES))
		orthanc.RegisterRestCallback(KAFKA_INSTANCES_PUSH_URL, OrthancKafkaExportView.as_view(
			sonador_manager=sonador_manager, sessionmaker=sessionmaker, resource_cachemodel=sonador_internaldb.Resource,
			kafka_topic=kafka_topic, kafka_opcode=SONADOR_KAFKA_OPCODE_PUSH_IMAGE))