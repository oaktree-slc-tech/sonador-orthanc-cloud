import logging
from confluent_kafka import Producer

from ..apisettings import KAFKA_TIMEOUT_DEFAULT, ORTHANC_CONFIG_SECTION_SONADOR, SONADOR_CONF_KAFKA, \
	SONADOR_CONF_KAFKA_SERVERS, SONADOR_CONF_KAFKA_TOPIC, SONADOR_KAFKA_BOOTSTRAP

from . import helpers as kafka_helpers

logger = logging.getLogger(__name__)


class SonadorProducer:
	'''	Sonador Kafka producer. Provides encapsulated methods for managing export
		of Orthanc data.
	'''
	def __init__(self, kafka_config):
		''' Initialize the Sonador producer instance
		'''
		self.config = kafka_config
		self.servers = kafka_helpers.get_kafka_servers(self.config)
		if not self.servers:
			raise ValueError('Unable to initialize Kafka connection, invalid server list')

		# Initialize producer instance
		self.producer = Producer({ SONADOR_KAFKA_BOOTSTRAP: self.servers })

		# Primary topic
		self.topic = self.config.get(SONADOR_CONF_KAFKA_TOPIC)
		if not self.topic:
			raise ValueError('Unable to initialize Kafka connection, invalid topic')

		logger.warning('Initialize Kafka producer: servers="%s" topic="%s"' % (self.servers, self.topic))

	def delivery_report(self, err, msg):
		'''	The Kafka producer delivers data asynchronously. This function is the
			callback by the Kafka client to indicate whether a message was delivered
			successfully or with an error. For successful deliveries, "err" will be None.
		'''
		import orthanc

		if err is not None:
			orthanc.LogError('Unable to deliver message to Kafka instance %s. Error: %s\n%s' % (
				err, kafka_servers, msg.value()
			))
			self.producer.produce(
				self.topic, msg.value(), callback=lambda err, msg: self.delivery_report(err, msg))

	def send_msg(self, msg, topic=None, callback=None):
		'''	Send message to the provided topic, defaut topic for the producer is used 
			if no topic is specified.
		'''
		self.producer.produce(topic or self.topic, msg, 
			callback=lambda err,msg: self.delivery_report(err, msg))


	def poll(self, *args, **kwargs):
		return self.producer.poll(*args, **kwargs)