import logging, abc, collections, json, threading, time
from confluent_kafka import Producer

from sonador.serialization import SonadorJsonEncoder

from ..apisettings import KAFKA_DELIVERY_MAX_ATTEMPTS, KAFKA_DELIVERY_RETRY_BACKOFF, \
	KAFKA_PENDING_MAX_MESSAGES, SONADOR_CONF_KAFKA_TOPIC, SONADOR_KAFKA_BOOTSTRAP

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

	# Messages the local producer queue refused synchronously, kept for re-enqueue.
	pending_max_messages = KAFKA_PENDING_MAX_MESSAGES

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
		self.pending = collections.deque()
		# Guards every read-modify-write of `pending`: request threads retain, the scheduler
		# thread retries, and shutdown flushes.
		self._pending_lock = threading.Lock()

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

	def _produce(self, topic, msg):
		self.producer.produce(topic, msg,
			callback=lambda err, m: self.delivery_report(err, m))

	def send_msg(self, msg, topic=None, callback=None):
		'''	Send message to the provided topic, defaut topic for the producer is used
			if no topic is specified.

			A message the local producer queue refuses synchronously (a full queue raises
			BufferError; librdkafka may raise for other transient reasons) is retained and
			re-enqueued from `poll()` / `flush()` rather than raised to the caller, so a request
			whose database work has already committed is not answered as a failure.

			@returns bool: True when the message was handed to the producer, False when retained
		'''
		topic = topic or self.topic

		try:
			self._produce(topic, msg)
			return True

		except Exception as err:
			self.retain(topic, msg, err)
			return False

	def retain(self, topic, msg, err=None):
		'''	Keep a message whose enqueue failed, for a later `retry_pending()`. The oldest
			retained message is dropped, and logged with its payload, once the bound is reached.
		'''
		with self._pending_lock:
			if len(self.pending) >= self.pending_max_messages:
				dropped_topic, dropped_msg = self.pending.popleft()
				logger.error('Kafka retention bound (%s) reached; dropping oldest message for topic "%s":\n%s'
					% (self.pending_max_messages, dropped_topic, dropped_msg))

			self.pending.append((topic, msg))
			retained = len(self.pending)

		logger.error('Unable to enqueue Kafka message for topic "%s"; retained for retry (%s pending). Error: %s\n%s'
			% (topic, retained, err, msg))

	def retry_pending(self):
		'''	Re-enqueue retained messages in order, stopping at the first the producer still refuses.

			@returns int: number of messages still retained
		'''
		while True:
			# Selection, enqueue and removal happen under one hold of the lock, so a concurrent
			# retain() (and its bounded eviction) can only run between items and never removes the
			# item this loop is handing to the producer. produce() only enqueues locally.
			with self._pending_lock:
				if not self.pending:
					return 0

				topic, msg = self.pending[0]

				try:
					self._produce(topic, msg)
				except Exception as err:
					logger.warning('Kafka producer still refusing retained message for topic "%s" (%s pending). Error: %s'
						% (topic, len(self.pending), err))
					return len(self.pending)

				self.pending.popleft()

	def poll(self, *args, **kwargs):
		'''	Service delivery reports, then re-enqueue anything retained. Polling first is what frees
			queue space after a BufferError.
		'''
		result = self.producer.poll(*args, **kwargs)
		self.retry_pending()

		return result

	def flush(self, timeout=None):
		'''	Block until every message queued locally has been delivered or finally failed,
			including messages held in retention.

			Called from the ORTHANC_STOPPED callback, which is the only place blocking on the
			broker is acceptable. Retained messages are re-enqueued and flushed in cycles until
			both queues are empty, the producer keeps refusing, or `timeout` (seconds, whole
			operation) is exhausted.

			@returns int: messages still outstanding, in the producer queue plus in retention
		'''
		deadline = None if timeout is None else time.monotonic() + timeout

		def _flush_producer():
			if deadline is None:
				return self.producer.flush()

			return self.producer.flush(max(0.0, deadline - time.monotonic()))

		self.retry_pending()
		outstanding = _flush_producer()

		while self.pending:
			if deadline is not None and time.monotonic() >= deadline:
				break

			before = len(self.pending)
			self.retry_pending()

			if len(self.pending) == before:
				# The producer refused even the head of the queue with an empty local queue
				# behind it; another cycle would not change that.
				break

			outstanding = _flush_producer()

		return outstanding + len(self.pending)

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
		return self.publish_kafka_data(_kafka)

	def publish_kafka_data(self, data):
		'''	Serialize and send an already-assembled message payload to the view's Kafka topic.

			Never raises: the callers publish after their database work has committed, and a
			publication problem must not turn a committed operation into an error response. The
			producer retains a message it could not enqueue; anything else is logged with the
			payload so it can be replayed.

			@returns dict / JSON object: the payload, or None when it could not be handed to the producer
		'''
		try:
			payload = json.dumps(data, cls=self.json_cls)
			self.sonador_manager.kafka_producer.send_msg(payload, topic=self.kafka_topic)
			return data

		except Exception as err:
			logger.error('Unable to publish Kafka message for topic "%s". Error: %s\nPayload: %r'
				% (self.kafka_topic, err, data))
			return None

	@abc.abstractmethod
	def fetch_kafka_data(self, *args, **kwargs):
		'''	Abstract method to retrieve resource, aggregate data, and prepare Kafka data to send to Kafka.
			Must be implemented in the view instance where the mixin is used.

			@returns dict / JSON object
		'''
