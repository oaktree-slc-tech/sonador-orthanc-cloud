import logging, abc, json
from confluent_kafka import Producer

from sonador.serialization import SonadorJsonEncoder

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


class KafkaMixin:
	''' Mixin class that initializes the Kafka context for a web view. Provides
		methods for serializing and sending data to Kafka.
		
		@attr sonador_manager_required_kafka (bool, default=True): when set on the view instance
			a check will be performed to ensure that a Sonador manager instance is available
			and that the manager provides a Kafka producer instance
		@attr sonador_manager (sonador_orthanc.manager.SonadorServerManager): server manager instance

		@attr kafka_topic_required (bool, default=True): when set on the view instance
			a check will be performed to ensure that a Kafka topic is available as part of
			init/setup.
		@attr kafka_topic (str, default=None): Kafka topic to which data should be sent
		@attr json_cls (JSON encoder cls, default=SonadorJsonEncoder)
	'''
	sonador_manager_required_kafka = True
	sonador_manager = None
	
	kafka_topic_required = True
	kafka_topic = None
	json_cls = SonadorJsonEncoder

	def _init_kafka(self, *args, **kwargs):
		self.kafka_topic = kwargs.get('kafka_topic', self.kafka_topic)
		self.json_cls = kwargs.get('json_cls', SonadorJsonEncoder)

		# Ensure that the Sonador manager instance is present and has a Kafka producer instance defined
		if self.sonador_manager_required_kafka:

			if self.sonador_manager is None:
				raise ConfigurationError(
					'Unable to initialize %s view to send data to Kafka: invalid Sonador manager instance' % type(self).__name__)

			if not getattr(self.sonador_manager, 'kafka_producer', None):
				raise ConfigurationError(('Unable to initialize %s view: Sonador manager instance does not have a Kafka producer '
					+ 'associated with it.') % type(self).__name__)

		# Ensure that a Kafka topic is associated with the view instance
		if self.kafka_topic_required and not self.kafka_topic:
			raise ConfigurationError('Unable to initiaze %s view, invalid kafka topic "%s"' % (type(self).__name__, self.kafka_topic))
	
	def send_kafka_msg(self, *args, **kwargs):
		'''	Serialize and send message data to Kafka. IMPORTANT: the signature for the "send_kafka_msg"
			method for a view instance should match that of `fetch_kafka_data`. The default method implementation
			in this mixin forwards all arugments without making any changes.

			@returns dict / JSON object: copy of the message payload sent to Kafka
		'''
		_kafka = self.fetch_kafka_data(*args, **kwargs)
		self.sonador_manager.kafka_producer.send_msg(
			json.dumps(_kafka, cls=self.json_cls), topic=self.kafka_topic)

		return _kafka

	@abc.abstractmethod
	def fetch_kafka_data(self, *args, **kwargs):
		'''	Abstract method to retrieve resource, aggregate data, and prepare Kafka data to send to Kafka.
			Must be implemented in the view instance where the mixin is used.

			@returns dict / JSON object
		'''
