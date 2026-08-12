'''	Unit tests for the Kafka producer configuration builder.

	These assert on the exact librdkafka property dict the plugin constructs, because that
	dict is the whole of the transport-security posture: a property that is silently
	dropped, or a credential that is silently ignored, is a connection that believes it is
	encrypted and is not.

	Run with:  python3 -m pytest tests
'''
import os, stat

import pytest

from conftest import RecordingProducer, load_kafka_module

helpers = load_kafka_module('helpers')
base = load_kafka_module('base')

from sonador_orthanc import apisettings


SERVERS = ['kafka:9093']
BOOTSTRAP = 'kafka:9093'
TOPIC = 'orthanc-index'

# A value that must never appear in a log line, at any level.
SENTINEL_PASSWORD = 'sentinel-Passw0rd-do-not-log'


@pytest.fixture
def certs(tmp_path):
	'''	A directory of readable stand-in credential files. Contents are irrelevant: the
		builder checks that the paths resolve and are readable, and hands them to
		librdkafka verbatim.
	'''
	paths = {}

	for name, contents in (('ca.pem', 'ca'), ('client.pem', 'cert'), ('client.key', 'key'),
			('sasl_password', SENTINEL_PASSWORD + '\n'), ('key_password', 'keyfile-secret\n')):
		path = tmp_path / name
		path.write_text(contents)
		paths[name] = str(path)

	return paths


def kafka_conf(security=None, servers=None, topic=TOPIC):
	'''	Build a `Sonador.Kafka` configuration block.
	'''
	conf = { 'servers': SERVERS if servers is None else servers, 'topic': topic }
	if security is not None:
		conf['security'] = security

	return conf


# ---------------------------------------------------------------------------------------
# AC-1: an unconfigured deployment is byte-for-byte what it was
# ---------------------------------------------------------------------------------------

def test_no_security_block_yields_bootstrap_servers_only():
	'''	AC-1 / FR-5: with no `security` block the builder produces exactly the single-key
		dict the plugin has always built.
	'''
	assert helpers.build_producer_config(kafka_conf()) == { 'bootstrap.servers': BOOTSTRAP }


def test_producer_is_constructed_with_bootstrap_servers_only():
	'''	AC-1, asserted where it actually matters: on the dict handed to
		`confluent_kafka.Producer`, not merely on the builder's return value.
	'''
	del RecordingProducer.instances[:]

	producer = base.SonadorProducer(kafka_conf())

	assert len(RecordingProducer.instances) == 1
	assert RecordingProducer.instances[0].config == { 'bootstrap.servers': BOOTSTRAP }
	assert producer.servers == BOOTSTRAP
	assert producer.topic == TOPIC


def test_empty_security_block_yields_bootstrap_servers_only():
	'''	A `security` block left in place but emptied out is not a misconfiguration.
	'''
	assert helpers.build_producer_config(kafka_conf(security={})) == { 'bootstrap.servers': BOOTSTRAP }


# ---------------------------------------------------------------------------------------
# AC-2: the protocol x credential-form matrix
# ---------------------------------------------------------------------------------------

def test_explicit_plaintext_protocol_is_emitted():
	'''	PLAINTEXT is librdkafka's default, but an operator who states it should see it.
	'''
	assert helpers.build_producer_config(kafka_conf(security={ 'protocol': 'PLAINTEXT' })) == {
		'bootstrap.servers': BOOTSTRAP,
		'security.protocol': 'PLAINTEXT',
	}


def test_ssl_with_ca_only(certs):
	'''	Server-authenticated TLS: the client verifies the broker and presents nothing.
	'''
	assert helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SSL',
		'ssl': { 'ca': certs['ca.pem'] },
	})) == {
		'bootstrap.servers': BOOTSTRAP,
		'security.protocol': 'SSL',
		'ssl.ca.location': certs['ca.pem'],
	}


def test_ssl_mutual_tls(certs):
	'''	AC-5's configuration: mutual TLS, no SASL.
	'''
	assert helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SSL',
		'ssl': {
			'ca': certs['ca.pem'],
			'certificate': certs['client.pem'],
			'key': certs['client.key'],
		},
	})) == {
		'bootstrap.servers': BOOTSTRAP,
		'security.protocol': 'SSL',
		'ssl.ca.location': certs['ca.pem'],
		'ssl.certificate.location': certs['client.pem'],
		'ssl.key.location': certs['client.key'],
	}


def test_ssl_key_password_inline(certs):
	'''	Credential form: inline.
	'''
	config = helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SSL',
		'ssl': {
			'ca': certs['ca.pem'],
			'certificate': certs['client.pem'],
			'key': certs['client.key'],
			'keyPassword': 'inline-secret',
		},
	}))

	assert config['ssl.key.password'] == 'inline-secret'


def test_ssl_key_password_from_file(certs):
	'''	Credential form: file reference. The trailing newline a secret file almost always
		carries is stripped; nothing else is.
	'''
	config = helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SSL',
		'ssl': {
			'ca': certs['ca.pem'],
			'certificate': certs['client.pem'],
			'key': certs['client.key'],
			'keyPasswordFile': certs['key_password'],
		},
	}))

	assert config['ssl.key.password'] == 'keyfile-secret'


def test_ssl_key_password_file_wins_over_inline(certs, caplog):
	'''	Credential form: both. The file wins, and the warning names the key and not the value.
	'''
	config = helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SSL',
		'ssl': {
			'ca': certs['ca.pem'],
			'certificate': certs['client.pem'],
			'key': certs['client.key'],
			'keyPassword': 'inline-secret',
			'keyPasswordFile': certs['key_password'],
		},
	}))

	assert config['ssl.key.password'] == 'keyfile-secret'
	assert 'keyPasswordFile' in caplog.text
	assert 'inline-secret' not in caplog.text
	assert 'keyfile-secret' not in caplog.text


def test_ssl_key_password_neither_form(certs):
	'''	Credential form: neither. An unencrypted private key needs no password, so the
		property is simply absent.
	'''
	config = helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SSL',
		'ssl': {
			'ca': certs['ca.pem'],
			'certificate': certs['client.pem'],
			'key': certs['client.key'],
		},
	}))

	assert 'ssl.key.password' not in config


def test_null_inline_credential_does_not_conflict_with_file(certs, caplog):
	'''	The documented schema carries `"keyPassword": null` beside a `keyPasswordFile`. A
		null must read as absent, not as a conflicting inline value.
	'''
	config = helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SSL',
		'ssl': {
			'ca': certs['ca.pem'],
			'keyPassword': None,
			'keyPasswordFile': certs['key_password'],
		},
	}))

	assert config['ssl.key.password'] == 'keyfile-secret'
	assert 'takes precedence' not in caplog.text


@pytest.mark.parametrize('verify_hostname,expected', [(True, 'https'), (False, 'none')])
def test_ssl_verify_hostname(certs, verify_hostname, expected):
	'''	FR-2: hostname verification can be disabled for brokers whose certificate does not
		match the service DNS name.
	'''
	config = helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SSL',
		'ssl': { 'ca': certs['ca.pem'], 'verifyHostname': verify_hostname },
	}))

	assert config['ssl.endpoint.identification.algorithm'] == expected


def test_ssl_verify_hostname_omitted_when_unset(certs):
	'''	Unstated means unstated: librdkafka's own default applies and nothing is invented.
	'''
	config = helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SSL',
		'ssl': { 'ca': certs['ca.pem'] },
	}))

	assert 'ssl.endpoint.identification.algorithm' not in config


def test_sasl_plaintext_with_inline_password():
	'''	SASL over a cleartext transport, credentials inline.
	'''
	assert helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SASL_PLAINTEXT',
		'sasl': { 'mechanism': 'SCRAM-SHA-512', 'username': 'orthanc', 'password': 'secret' },
	})) == {
		'bootstrap.servers': BOOTSTRAP,
		'security.protocol': 'SASL_PLAINTEXT',
		'sasl.mechanism': 'SCRAM-SHA-512',
		'sasl.username': 'orthanc',
		'sasl.password': 'secret',
	}


def test_sasl_plaintext_with_password_file(certs):
	'''	SASL over a cleartext transport, credentials by file reference.
	'''
	config = helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SASL_PLAINTEXT',
		'sasl': { 'mechanism': 'PLAIN', 'username': 'orthanc', 'passwordFile': certs['sasl_password'] },
	}))

	assert config['sasl.mechanism'] == 'PLAIN'
	assert config['sasl.password'] == SENTINEL_PASSWORD


def test_sasl_ssl_full_configuration(certs):
	'''	AC-4's configuration: SASL_SSL with SCRAM-SHA-512 and a file-referenced password,
		which is the shape a correct deployment takes (AR-3).
	'''
	assert helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SASL_SSL',
		'ssl': { 'ca': certs['ca.pem'], 'verifyHostname': False },
		'sasl': {
			'mechanism': 'SCRAM-SHA-512',
			'username': 'orthanc',
			'password': None,
			'passwordFile': certs['sasl_password'],
		},
	})) == {
		'bootstrap.servers': BOOTSTRAP,
		'security.protocol': 'SASL_SSL',
		'ssl.ca.location': certs['ca.pem'],
		'ssl.endpoint.identification.algorithm': 'none',
		'sasl.mechanism': 'SCRAM-SHA-512',
		'sasl.username': 'orthanc',
		'sasl.password': SENTINEL_PASSWORD,
	}


@pytest.mark.parametrize('mechanism', ['GSSAPI', 'OAUTHBEARER'])
def test_sasl_mechanisms_without_client_credentials(certs, mechanism):
	'''	GSSAPI authenticates from a keytab and OAUTHBEARER from a token, so neither is
		required to carry a username and password.
	'''
	assert helpers.build_producer_config(kafka_conf(security={
		'protocol': 'SASL_SSL',
		'ssl': { 'ca': certs['ca.pem'] },
		'sasl': { 'mechanism': mechanism },
	})) == {
		'bootstrap.servers': BOOTSTRAP,
		'security.protocol': 'SASL_SSL',
		'ssl.ca.location': certs['ca.pem'],
		'sasl.mechanism': mechanism,
	}


def test_protocol_and_mechanism_are_case_normalized():
	'''	Kafka spells these in upper case; an operator who does not should still get a
		working client rather than a startup failure.
	'''
	config = helpers.build_producer_config(kafka_conf(security={
		'protocol': 'sasl_plaintext',
		'sasl': { 'mechanism': 'scram-sha-256', 'username': 'orthanc', 'password': 'secret' },
	}))

	assert config['security.protocol'] == 'SASL_PLAINTEXT'
	assert config['sasl.mechanism'] == 'SCRAM-SHA-256'


def test_every_supported_protocol_is_accepted(certs):
	'''	FR-1: all four protocols named in the requirement build without error.
	'''
	security_for = {
		'PLAINTEXT': {},
		'SSL': { 'ssl': { 'ca': certs['ca.pem'] } },
		'SASL_PLAINTEXT': { 'sasl': { 'mechanism': 'PLAIN', 'username': 'u', 'password': 'p' } },
		'SASL_SSL': {
			'ssl': { 'ca': certs['ca.pem'] },
			'sasl': { 'mechanism': 'PLAIN', 'username': 'u', 'password': 'p' },
		},
	}

	for protocol in apisettings.SONADOR_KAFKA_PROTOCOL_SUPPORTED:
		security = dict(security_for[protocol], protocol=protocol)
		config = helpers.build_producer_config(kafka_conf(security=security))

		assert config['security.protocol'] == protocol


def test_every_supported_mechanism_is_accepted():
	'''	FR-3: all five mechanisms named in the requirement build without error.
	'''
	for mechanism in apisettings.SONADOR_KAFKA_SASL_MECHANISM_SUPPORTED:
		sasl = { 'mechanism': mechanism }
		if mechanism in apisettings.SONADOR_KAFKA_SASL_MECHANISM_CREDENTIALED:
			sasl.update(username='orthanc', password='secret')

		config = helpers.build_producer_config(kafka_conf(security={
			'protocol': 'SASL_PLAINTEXT', 'sasl': sasl }))

		assert config['sasl.mechanism'] == mechanism


# ---------------------------------------------------------------------------------------
# AC-3: every rejection names the offending key
# ---------------------------------------------------------------------------------------

def test_unrecognised_protocol_is_rejected():
	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(security={ 'protocol': 'SASL_TLS' }))

	assert 'protocol' in str(err.value)
	assert 'SASL_TLS' in str(err.value)


def test_unrecognised_mechanism_is_rejected():
	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(security={
			'protocol': 'SASL_PLAINTEXT', 'sasl': { 'mechanism': 'SCRAM-SHA-1' } }))

	assert 'mechanism' in str(err.value)
	assert 'SCRAM-SHA-1' in str(err.value)


def test_sasl_protocol_without_mechanism_is_rejected():
	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(security={ 'protocol': 'SASL_PLAINTEXT' }))

	assert 'mechanism' in str(err.value)


def test_credentialed_mechanism_without_username_is_rejected():
	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(security={
			'protocol': 'SASL_PLAINTEXT',
			'sasl': { 'mechanism': 'SCRAM-SHA-512', 'password': 'secret' } }))

	assert 'username' in str(err.value)


def test_credentialed_mechanism_without_password_is_rejected():
	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(security={
			'protocol': 'SASL_PLAINTEXT',
			'sasl': { 'mechanism': 'SCRAM-SHA-512', 'username': 'orthanc' } }))

	assert 'password' in str(err.value)


def test_certificate_without_key_is_rejected(certs):
	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(security={
			'protocol': 'SSL',
			'ssl': { 'ca': certs['ca.pem'], 'certificate': certs['client.pem'] } }))

	assert 'certificate' in str(err.value)
	assert 'key' in str(err.value)


def test_key_without_certificate_is_rejected(certs):
	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(security={
			'protocol': 'SSL',
			'ssl': { 'ca': certs['ca.pem'], 'key': certs['client.key'] } }))

	assert 'certificate' in str(err.value)
	assert 'key' in str(err.value)


@pytest.mark.parametrize('key', ['ca', 'certificate', 'key'])
def test_missing_certificate_path_is_rejected_at_startup(tmp_path, certs, key):
	'''	FR-6: the unmounted-volume mistake fails at startup naming the file, rather than as
		a connection that never delivers.
	'''
	ssl = { 'ca': certs['ca.pem'], 'certificate': certs['client.pem'], 'key': certs['client.key'] }
	ssl[key] = str(tmp_path / 'absent.pem')

	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(security={ 'protocol': 'SSL', 'ssl': ssl }))

	assert key in str(err.value)
	assert 'absent.pem' in str(err.value)


def test_missing_password_file_is_rejected_at_startup(tmp_path):
	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(security={
			'protocol': 'SASL_PLAINTEXT',
			'sasl': {
				'mechanism': 'PLAIN',
				'username': 'orthanc',
				'passwordFile': str(tmp_path / 'absent-secret'),
			} }))

	assert 'passwordFile' in str(err.value)
	assert 'absent-secret' in str(err.value)


def test_empty_password_file_is_rejected(tmp_path):
	'''	An empty secret file is the signature of a Kubernetes Secret key that does not
		exist, and produces an authentication failure with no other diagnostic.
	'''
	secret = tmp_path / 'empty-secret'
	secret.write_text('\n')

	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(security={
			'protocol': 'SASL_PLAINTEXT',
			'sasl': { 'mechanism': 'PLAIN', 'username': 'orthanc', 'passwordFile': str(secret) } }))

	assert 'passwordFile' in str(err.value)
	assert 'empty' in str(err.value)


@pytest.mark.skipif(hasattr(os, 'geteuid') and os.geteuid() == 0,
	reason='root bypasses the read permission bit, so an unreadable file cannot be simulated')
def test_unreadable_certificate_is_rejected(tmp_path):
	'''	A certificate mounted with the wrong ownership is as fatal as one not mounted at all.
	'''
	ca = tmp_path / 'ca.pem'
	ca.write_text('ca')
	os.chmod(str(ca), stat.S_IWUSR)

	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(security={
			'protocol': 'SSL', 'ssl': { 'ca': str(ca) } }))

	assert 'not readable' in str(err.value)
	assert 'ca' in str(err.value)


@pytest.mark.parametrize('protocol,block,contents', [
	('PLAINTEXT', 'ssl', { 'ca': '/dev/null' }),
	('PLAINTEXT', 'sasl', { 'mechanism': 'PLAIN', 'username': 'u', 'password': 'p' }),
	('SSL', 'sasl', { 'mechanism': 'PLAIN', 'username': 'u', 'password': 'p' }),
	('SASL_PLAINTEXT', 'ssl', { 'ca': '/dev/null' }),
])
def test_security_settings_under_an_incompatible_protocol_are_rejected(protocol, block, contents):
	'''	Credentials configured under a protocol that cannot use them are a misconfiguration,
		not something to drop silently: silently dropping them is how a deployment comes to
		believe it is encrypted or authenticated when it is neither.
	'''
	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(security={ 'protocol': protocol, block: contents }))

	assert block in str(err.value)
	assert protocol in str(err.value)


def test_empty_server_list_is_rejected():
	with pytest.raises(ValueError) as err:
		helpers.build_producer_config(kafka_conf(servers=[]))

	assert 'server list' in str(err.value)


# ---------------------------------------------------------------------------------------
# FR-7: no secret reaches a log line
# ---------------------------------------------------------------------------------------

def test_redact_producer_config_masks_every_secret_property():
	config = {
		'bootstrap.servers': BOOTSTRAP,
		'security.protocol': 'SASL_SSL',
		'ssl.ca.location': '/etc/orthanc/certs/ca.pem',
		'ssl.key.password': SENTINEL_PASSWORD,
		'sasl.username': 'orthanc',
		'sasl.password': SENTINEL_PASSWORD,
	}

	redacted = helpers.redact_producer_config(config)

	assert SENTINEL_PASSWORD not in repr(redacted)
	assert redacted['ssl.key.password'] == apisettings.SONADOR_KAFKA_SECRET_REDACTED
	assert redacted['sasl.password'] == apisettings.SONADOR_KAFKA_SECRET_REDACTED

	# Non-secret properties are needed to diagnose a broker problem and are left intact.
	assert redacted['bootstrap.servers'] == BOOTSTRAP
	assert redacted['security.protocol'] == 'SASL_SSL'
	assert redacted['ssl.ca.location'] == '/etc/orthanc/certs/ca.pem'
	assert redacted['sasl.username'] == 'orthanc'

	# The original is not mutated: it is what gets handed to librdkafka.
	assert config['sasl.password'] == SENTINEL_PASSWORD


def test_every_secret_property_is_one_the_builder_can_emit():
	'''	Guards the redaction list against drift: a secret property added to the builder and
		not to SONADOR_KAFKA_SECRET_PROPERTIES would silently start reaching the log.
	'''
	assert apisettings.SONADOR_KAFKA_SECRET_PROPERTIES == frozenset((
		'ssl.key.password', 'sasl.password'))


def test_producer_startup_log_does_not_carry_the_password(certs, caplog):
	'''	FR-7 at the one place the plugin logs a producer configuration.
	'''
	import logging

	caplog.set_level(logging.DEBUG)

	base.SonadorProducer(kafka_conf(security={
		'protocol': 'SASL_SSL',
		'ssl': { 'ca': certs['ca.pem'] },
		'sasl': {
			'mechanism': 'SCRAM-SHA-512',
			'username': 'orthanc',
			'passwordFile': certs['sasl_password'],
		},
	}))

	assert SENTINEL_PASSWORD not in caplog.text

	# The log is still useful: it names the broker, the protocol and the mechanism.
	assert BOOTSTRAP in caplog.text
	assert 'SASL_SSL' in caplog.text
	assert 'SCRAM-SHA-512' in caplog.text


# ---------------------------------------------------------------------------------------
# Delivery reporting and shutdown
# ---------------------------------------------------------------------------------------

class FakeMessage:
	'''	Minimal stand-in for a librdkafka message as handed to a delivery report.
	'''
	def __init__(self, topic, value):
		self._topic = topic
		self._value = value

	def topic(self):
		return self._topic

	def value(self):
		return self._value


class ImmediateTimer:
	'''	`threading.Timer` replacement that runs the callback on `start()`, so the backoff
		does not have to be waited out.
	'''
	def __init__(self, interval, function):
		self.interval = interval
		self.function = function
		self.daemon = False

	def start(self):
		self.function()


@pytest.fixture
def orthanc_log(monkeypatch):
	'''	Replace the Orthanc SDK's logging entry points with recorders. The stub `orthanc`
		module accepts any call, but only a recorder lets a test read what was logged.
	'''
	import sys, types

	records = []

	module = types.ModuleType('orthanc')
	module.LogError = lambda message: records.append(message)
	module.LogWarning = lambda message: records.append(message)
	module.LogInfo = lambda message: records.append(message)

	monkeypatch.setitem(sys.modules, 'orthanc', module)

	return records


@pytest.fixture
def immediate_backoff(monkeypatch):
	monkeypatch.setattr(base.threading, 'Timer', ImmediateTimer)


def test_delivery_report_logs_the_failure(orthanc_log):
	'''	The error branch previously raised NameError on an undefined `kafka_servers`, so a
		delivery failure could not be logged at all and the retry below it was unreachable.
		With TLS and SASL in play this line is the only diagnostic for an expired
		certificate or a rotated credential.
	'''
	producer = base.SonadorProducer(kafka_conf())

	producer.delivery_report('broker rejected the client', FakeMessage(TOPIC, b'payload'))

	assert any('broker rejected the client' in record for record in orthanc_log)
	assert any(BOOTSTRAP in record for record in orthanc_log)


def test_delivery_report_is_a_no_op_on_success(orthanc_log):
	producer = base.SonadorProducer(kafka_conf())

	producer.delivery_report(None, FakeMessage(TOPIC, b'payload'))

	assert orthanc_log == []


def test_delivery_retry_preserves_the_original_topic(orthanc_log, immediate_backoff, monkeypatch):
	'''	`send_msg` accepts a per-message topic. Re-producing to the producer default would
		silently reroute a worklist or comment message onto the index stream.
	'''
	producer = base.SonadorProducer(kafka_conf())

	produced = []
	monkeypatch.setattr(producer.producer, 'produce',
		lambda topic, payload, **kwargs: produced.append((topic, payload)), raising=False)

	producer.delivery_report('failed', FakeMessage('study-comments', b'payload'))

	assert produced == [('study-comments', b'payload')]


def test_delivery_retry_is_bounded(orthanc_log, immediate_backoff, monkeypatch):
	'''	Left unbounded, a broker rejecting the client's credentials turns the delivery
		callback into an infinite re-produce loop.
	'''
	producer = base.SonadorProducer(kafka_conf())

	produced = []

	def _produce(topic, payload, **kwargs):
		produced.append((topic, payload))

		# Every attempt fails, the way a rejected credential does.
		kwargs['callback']('failed', FakeMessage(topic, payload))

	monkeypatch.setattr(producer.producer, 'produce', _produce, raising=False)

	producer.delivery_report('failed', FakeMessage(TOPIC, b'payload'))

	# The first attempt is the one the caller reported; the retries are what this bounds.
	assert len(produced) == producer.delivery_max_attempts - 1
	assert any('Abandon Kafka message' in record for record in orthanc_log)


def test_delivery_backoff_grows_with_each_attempt(orthanc_log, monkeypatch):
	intervals = []

	class RecordingTimer(ImmediateTimer):
		def __init__(self, interval, function):
			intervals.append(interval)
			ImmediateTimer.__init__(self, interval, function)

	monkeypatch.setattr(base.threading, 'Timer', RecordingTimer)

	producer = base.SonadorProducer(kafka_conf())
	monkeypatch.setattr(producer.producer, 'produce',
		lambda topic, payload, **kwargs: kwargs['callback']('failed', FakeMessage(topic, payload)),
		raising=False)

	producer.delivery_report('failed', FakeMessage(TOPIC, b'payload'))

	assert intervals == [producer.delivery_retry_backoff, producer.delivery_retry_backoff * 2]


def test_producer_exposes_flush():
	'''	AC-10: `orthanc_kafka_onstop` calls `flush()` on ORTHANC_STOPPED. Without the
		passthrough, shutdown raised AttributeError and every queued message was dropped.
	'''
	producer = base.SonadorProducer(kafka_conf())

	assert producer.flush() == 0


# ---------------------------------------------------------------------------------------
# get_kafka_servers
# ---------------------------------------------------------------------------------------

def test_get_kafka_servers_accepts_a_list():
	assert helpers.get_kafka_servers({ 'servers': ['a:9092', 'b:9092'] }) == 'a:9092,b:9092'


def test_get_kafka_servers_accepts_a_string():
	'''	The string branch previously raised NameError on an undefined `CONF_KAFKA` and an
		unimported `six`, so it failed for anyone who configured `servers` as a string.
	'''
	assert helpers.get_kafka_servers({ 'servers': 'a:9092,b:9092' }) == 'a:9092,b:9092'


@pytest.mark.parametrize('conf', [{}, None, { 'servers': [] }, { 'servers': None }, { 'servers': 7 }])
def test_get_kafka_servers_returns_none_when_unusable(conf):
	assert helpers.get_kafka_servers(conf) is None
