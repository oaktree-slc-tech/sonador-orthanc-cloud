import logging, abc, json, threading
from confluent_kafka import Producer

from sonador.serialization import SonadorJsonEncoder

from ..apisettings import KAFKA_DELIVERY_MAX_ATTEMPTS, KAFKA_DELIVERY_RETRY_BACKOFF, \
	SONADOR_CONF_KAFKA_TOPIC, SONADOR_KAFKA_BOOTSTRAP

from . import helpers as kafka_helpers

logger = logging.getLogger(__name__)


class SonadorProducer:
	'''	Sonador Kafka producer. Provides encapsulated methods for managing export
		of Orthanc data.
	'''
	# Bound and backoff for the application-level delivery retry. See
	# `delivery_report` and the note on KAFKA_DELIVERY_MAX_ATTEMPTS in apisettings.
	delivery_max_attempts = KAFKA_DELIVERY_MAX_ATTEMPTS
	delivery_retry_backoff = KAFKA_DELIVERY_RETRY_BACKOFF

	def __init__(self, kafka_config):
		''' Initialize the Sonador producer instance
		'''
		self.config = kafka_config

		# Single parse of the `Sonador.Kafka` block: `build_producer_config` validates the
		# server list and the optional transport-security settings and returns the complete
		# set of librdkafka properties. Nothing else in the plugin reads this block.
		producer_config = kafka_helpers.build_producer_config(self.config)
		self.servers = producer_config[SONADOR_KAFKA_BOOTSTRAP]

		# Initialize producer instance
		self.producer = Producer(producer_config)

		# Primary topic
		self.topic = (self.config or {}).get(SONADOR_CONF_KAFKA_TOPIC)
		if not self.topic:
			raise ValueError('Unable to initialize Kafka connection, invalid topic')

		# The configuration is logged through `redact_producer_config` so that a key password
		# or SASL password set in the Orthanc JSON cannot reach the Orthanc log.
		logger.warning('Initialize Kafka producer: topic="%s" config=%r'
			% (self.topic, kafka_helpers.redact_producer_config(producer_config)))

	def delivery_report(self, err, msg, attempt=1):
		'''	The Kafka producer delivers data asynchronously. This function is the
			callback by the Kafka client to indicate whether a message was delivered
			successfully or with an error. For successful deliveries, "err" will be None.

			A failed delivery is re-produced at most `delivery_max_attempts` times, with an
			exponential backoff. librdkafka has already exhausted its own retry policy by the
			time this is called, so an unbounded re-produce here would amplify a broker
			outage rather than survive it -- and against a broker that is rejecting the
			client's credentials outright (an expired certificate, a rotated SASL password)
			it would never terminate.

			@input err (exception, None for successful deliveries): error report from the
				Kafka producer client
			@input msg (message instance): message the report concerns
			@input attempt (int): 1-based count of the delivery attempt this report concerns
		'''
		import orthanc

		if err is None:
			return

		# The topic is taken from the message rather than from `self.topic`: `send_msg`
		# accepts a per-message topic, and re-producing to the producer's default would
		# silently reroute a worklist or comment message onto the index stream.
		topic = msg.topic() if msg is not None else self.topic
		payload = msg.value() if msg is not None else None

		orthanc.LogError('Unable to deliver message to Kafka instance %s (topic "%s", attempt %s of %s). '
			'Error: %s\n%s' % (self.servers, topic, attempt, self.delivery_max_attempts, err, payload))

		if payload is None or attempt >= self.delivery_max_attempts:
			orthanc.LogError('Abandon Kafka message to topic "%s" after %s delivery attempt(s).'
				% (topic, attempt))
			return

		def _requeue():
			try:
				self.producer.produce(topic, payload,
					callback=lambda err, msg: self.delivery_report(err, msg, attempt=attempt + 1))

			except Exception as requeue_err:
				orthanc.LogError('Unable to requeue Kafka message to topic "%s" for delivery attempt %s: %s'
					% (topic, attempt + 1, requeue_err))

		# The backoff runs on a timer rather than inline. Delivery reports are serviced from
		# `poll()` and `flush()`, so sleeping here would stall the 30s flush task -- and
		# shutdown -- once per failed message in the queue.
		timer = threading.Timer(self.delivery_retry_backoff * (2 ** (attempt - 1)), _requeue)
		timer.daemon = True
		timer.start()

	def send_msg(self, msg, topic=None, callback=None):
		'''	Send message to the provided topic, defaut topic for the producer is used
			if no topic is specified.
		'''
		self.producer.produce(topic or self.topic, msg,
			callback=lambda err,msg: self.delivery_report(err, msg))


	def poll(self, *args, **kwargs):
		return self.producer.poll(*args, **kwargs)

	def flush(self, *args, **kwargs):
		'''	Block until every message queued locally has been delivered or finally failed.

			Called from the ORTHANC_STOPPED callback, which is the only place blocking on the
			broker is acceptable. Without this passthrough that callback raises AttributeError
			and every queued message is dropped on shutdown.
		'''
		return self.producer.flush(*args, **kwargs)

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
