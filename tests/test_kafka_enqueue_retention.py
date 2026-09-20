'''	Unit tests for the producer's handling of a synchronous enqueue failure.

	`Producer.produce()` raises when librdkafka's local queue is full (BufferError). The plugin's
	callers publish after their database work has committed, so the producer keeps the refused
	message and re-enqueues it from the scheduled `poll()` instead of raising.

	Run with:  python3 -m pytest tests
'''
import json, logging, threading

import pytest

from conftest import RecordingProducer, load_kafka_module

base = load_kafka_module('base')


TOPIC = 'orthanc-index'
CONF = { 'servers': ['kafka:9093'], 'topic': TOPIC }


class RefusingProducer(RecordingProducer):
	'''	Stand-in whose `produce()` raises BufferError for the first `refusals` calls, then accepts.
	'''
	refusals = 0

	def __init__(self, config):
		super().__init__(config)
		self.produced = []

	def produce(self, topic, value, *args, **kwargs):
		if RefusingProducer.refusals > 0:
			RefusingProducer.refusals -= 1
			raise BufferError('Local: Queue full')

		self.produced.append((topic, value))


@pytest.fixture
def producer(monkeypatch):
	monkeypatch.setattr(base, 'Producer', RefusingProducer)
	RefusingProducer.refusals = 0
	return base.SonadorProducer(CONF)


def test_accepted_message_is_not_retained(producer):
	assert producer.send_msg('{"a": 1}') is True
	assert producer.producer.produced == [(TOPIC, '{"a": 1}')]
	assert len(producer.pending) == 0


def test_refused_message_is_retained_not_raised(producer, caplog):
	RefusingProducer.refusals = 1

	with caplog.at_level(logging.ERROR):
		assert producer.send_msg('{"ID": "c1"}', topic='other') is False

	assert list(producer.pending) == [('other', '{"ID": "c1"}')]
	assert producer.producer.produced == []
	# The payload is in the log, so a message that is never re-enqueued can still be replayed.
	assert '{"ID": "c1"}' in caplog.text


def test_poll_re_enqueues_retained_messages_in_order(producer):
	RefusingProducer.refusals = 2
	producer.send_msg('m1')
	producer.send_msg('m2')
	assert [m for _, m in producer.pending] == ['m1', 'm2']

	producer.poll(0)

	assert len(producer.pending) == 0
	assert [m for _, m in producer.producer.produced] == ['m1', 'm2']


def test_retry_stops_at_first_still_refused_message(producer):
	RefusingProducer.refusals = 1
	producer.send_msg('m1')

	# The queue is still full on the retry: nothing is lost and order is kept.
	RefusingProducer.refusals = 1
	assert producer.retry_pending() == 1
	assert [m for _, m in producer.pending] == ['m1']

	assert producer.retry_pending() == 0
	assert [m for _, m in producer.producer.produced] == ['m1']


def test_flush_drains_retained_messages(producer):
	RefusingProducer.refusals = 1
	producer.send_msg('m1')

	producer.flush()

	assert len(producer.pending) == 0
	assert [m for _, m in producer.producer.produced] == ['m1']


def test_retention_is_bounded_and_drops_oldest(producer, caplog):
	producer.pending_max_messages = 2
	RefusingProducer.refusals = 3

	with caplog.at_level(logging.ERROR):
		for m in ('m1', 'm2', 'm3'):
			producer.send_msg(m)

	assert [m for _, m in producer.pending] == ['m2', 'm3']
	assert 'dropping oldest' in caplog.text and 'm1' in caplog.text


class _View(base.KafkaMixin):
	'''	Minimal view for the mixin: a manager whose producer is the object under test.
	'''
	sonador_manager_required_kafka = False
	kafka_topic_required = False

	def __init__(self, kafka_producer, topic=TOPIC):
		self.sonador_manager = type('Manager', (), { 'kafka_producer': kafka_producer })()
		self.kafka_topic = topic
		self.json_cls = json.JSONEncoder


def test_publish_returns_payload_when_enqueued(producer):
	view = _View(producer)

	assert view.publish_kafka_data({ 'ID': 'c1' }) == { 'ID': 'c1' }
	assert producer.producer.produced == [(TOPIC, '{"ID": "c1"}')]


def test_publish_does_not_raise_when_producer_refuses(producer):
	RefusingProducer.refusals = 1
	view = _View(producer)

	# A refused enqueue is retained by the producer; the caller still gets its payload back.
	assert view.publish_kafka_data({ 'ID': 'c1' }) == { 'ID': 'c1' }
	assert [m for _, m in producer.pending] == ['{"ID": "c1"}']


def test_publish_does_not_raise_on_any_producer_error(caplog):
	class Broken:
		def send_msg(self, *args, **kwargs):
			raise RuntimeError('producer gone')

	view = _View(Broken())

	with caplog.at_level(logging.ERROR):
		assert view.publish_kafka_data({ 'ID': 'c1' }) is None

	assert 'producer gone' in caplog.text and "'ID': 'c1'" in caplog.text


class CapacityProducer(RecordingProducer):
	'''	Stand-in with a bounded local queue: `produce()` raises BufferError once `capacity`
		messages are queued, and `flush()` delivers everything queued.
	'''
	capacity = 1

	def __init__(self, config):
		super().__init__(config)
		self.queued = []
		self.delivered = []

	def produce(self, topic, value, *args, **kwargs):
		if len(self.queued) >= CapacityProducer.capacity:
			raise BufferError('Local: Queue full')

		self.queued.append((topic, value))

	def flush(self, *args, **kwargs):
		self.delivered.extend(self.queued)
		self.queued = []
		return 0


@pytest.fixture
def capacity_producer(monkeypatch):
	monkeypatch.setattr(base, 'Producer', CapacityProducer)
	CapacityProducer.capacity = 1
	return base.SonadorProducer(CONF)


def test_flush_drains_a_backlog_larger_than_producer_capacity(capacity_producer):
	producer = capacity_producer
	producer.pending.extend([(TOPIC, 'a'), (TOPIC, 'b'), (TOPIC, 'c')])

	assert producer.flush() == 0
	assert [m for _, m in producer.producer.delivered] == ['a', 'b', 'c']
	assert len(producer.pending) == 0


def test_flush_reports_retained_messages_when_producer_keeps_refusing(capacity_producer):
	producer = capacity_producer
	CapacityProducer.capacity = 0
	producer.pending.extend([(TOPIC, 'a'), (TOPIC, 'b')])

	assert producer.flush() == 2
	assert [m for _, m in producer.pending] == ['a', 'b']
	assert producer.producer.delivered == []


def test_flush_stops_at_the_deadline_and_reports_the_remainder(monkeypatch, capacity_producer):
	producer = capacity_producer
	producer.pending.extend([(TOPIC, 'a'), (TOPIC, 'b'), (TOPIC, 'c')])

	clock = { 'now': 100.0 }
	monkeypatch.setattr(base.time, 'monotonic', lambda: clock['now'])
	# Each producer flush consumes more than the whole budget.
	original_flush = producer.producer.flush

	def slow_flush(*args, **kwargs):
		clock['now'] += 5.0
		return original_flush(*args, **kwargs)

	producer.producer.flush = slow_flush

	remainder = producer.flush(timeout=1.0)

	assert remainder == 2
	assert [m for _, m in producer.producer.delivered] == ['a']
	assert [m for _, m in producer.pending] == ['b', 'c']


class PausingProducer(RecordingProducer):
	'''	Stand-in whose `produce()` blocks on an event for the first message, so a concurrent
		retain() can be interleaved deterministically with a retry in progress.
	'''
	pause_on = None
	paused = threading.Event()
	resume = threading.Event()

	def __init__(self, config):
		super().__init__(config)
		self.produced = []

	def produce(self, topic, value, *args, **kwargs):
		if value == PausingProducer.pause_on:
			PausingProducer.pause_on = None
			PausingProducer.paused.set()
			PausingProducer.resume.wait(5)

		self.produced.append((topic, value))


def test_concurrent_retain_cannot_evict_the_message_a_retry_is_producing(monkeypatch):
	monkeypatch.setattr(base, 'Producer', PausingProducer)
	PausingProducer.pause_on = 'a'
	PausingProducer.paused.clear()
	PausingProducer.resume.clear()

	producer = base.SonadorProducer(CONF)
	producer.pending_max_messages = 2
	producer.pending.extend([(TOPIC, 'a'), (TOPIC, 'b')])

	retry = threading.Thread(target=producer.retry_pending)
	retry.start()
	assert PausingProducer.paused.wait(5), 'retry never reached the producer'

	# A request thread retains a third message while the retry holds "a". With the bound at two
	# it would evict the head; the lock makes it wait for the retry to finish with "a" first.
	retainer = threading.Thread(target=producer.retain, args=(TOPIC, 'c', BufferError('full')))
	retainer.start()
	retainer.join(0.2)
	assert retainer.is_alive(), 'retain() did not wait for the retry in progress'

	PausingProducer.resume.set()
	retry.join(5)
	retainer.join(5)
	assert not retry.is_alive() and not retainer.is_alive()

	# "a" was produced exactly once and was never evicted from under the retry. Whether the
	# retained "c" was picked up by the same retry pass or is still waiting depends on thread
	# scheduling; either way every message is produced or retained, once, and nothing is dropped.
	produced = [m for _, m in producer.producer.produced]
	retained = [m for _, m in producer.pending]
	assert produced[:2] == ['a', 'b']
	assert sorted(produced + retained) == ['a', 'b', 'c']
	assert len(produced + retained) == 3
