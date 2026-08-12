'''	Construction and validation of the librdkafka client configuration used by
	`sonador_orthanc.kafka.base.SonadorProducer`.

	`build_producer_config` is the *only* place the `Sonador.Kafka` block is turned into
	producer properties. Everything downstream -- the export views, the change callbacks,
	the worklist and comment views -- reads the topic and server list off the producer
	instance rather than re-reading the configuration, so adding a librdkafka property
	later means editing one function in one file.

	Validation is deliberately strict and runs at plugin startup, from
	`init_kafka_producer`, where a raised exception is visible in the Orthanc log. A
	configuration that cannot work should fail there rather than as a producer that
	accepts messages and never delivers them: with TLS and SASL in play the common
	deployment mistake is an unmounted certificate volume, whose only other symptom is
	silence.

	This module must stay importable without the `orthanc` module: it is the unit under
	test for the configuration matrix, and the Orthanc SDK only exists inside the server.
'''
import logging, os

from ..apisettings import SONADOR_CONF_KAFKA_SERVERS, SONADOR_KAFKA_BOOTSTRAP, \
	SONADOR_CONF_KAFKA_SECURITY, SONADOR_CONF_KAFKA_SECURITY_PROTOCOL, \
	SONADOR_CONF_KAFKA_SSL, SONADOR_CONF_KAFKA_SSL_CA, SONADOR_CONF_KAFKA_SSL_CERT, \
	SONADOR_CONF_KAFKA_SSL_KEY, SONADOR_CONF_KAFKA_SSL_KEY_PASSWORD, \
	SONADOR_CONF_KAFKA_SSL_KEY_PASSWORD_FILE, SONADOR_CONF_KAFKA_SSL_VERIFY_HOSTNAME, \
	SONADOR_CONF_KAFKA_SASL, SONADOR_CONF_KAFKA_SASL_MECHANISM, SONADOR_CONF_KAFKA_SASL_USERNAME, \
	SONADOR_CONF_KAFKA_SASL_PASSWORD, SONADOR_CONF_KAFKA_SASL_PASSWORD_FILE, \
	SONADOR_KAFKA_SECURITY_PROTOCOL, SONADOR_KAFKA_SSL_CA_LOCATION, \
	SONADOR_KAFKA_SSL_CERTIFICATE_LOCATION, SONADOR_KAFKA_SSL_KEY_LOCATION, \
	SONADOR_KAFKA_SSL_KEY_PASSWORD, SONADOR_KAFKA_SSL_ENDPOINT_ALGORITHM, \
	SONADOR_KAFKA_SSL_ENDPOINT_ALGORITHM_HTTPS, SONADOR_KAFKA_SSL_ENDPOINT_ALGORITHM_NONE, \
	SONADOR_KAFKA_SASL_MECHANISM, SONADOR_KAFKA_SASL_USERNAME, SONADOR_KAFKA_SASL_PASSWORD, \
	SONADOR_KAFKA_PROTOCOL_PLAINTEXT, SONADOR_KAFKA_PROTOCOL_SUPPORTED, \
	SONADOR_KAFKA_SASL_MECHANISM_SUPPORTED, SONADOR_KAFKA_SASL_MECHANISM_CREDENTIALED, \
	SONADOR_KAFKA_SECRET_PROPERTIES, SONADOR_KAFKA_SECRET_REDACTED

logger = logging.getLogger(__name__)

# Configuration path prefix used in error messages, so an operator reading a startup failure
# knows which part of the Orthanc JSON to open.
CONF_PATH_SECURITY = 'Sonador.Kafka.%s' % SONADOR_CONF_KAFKA_SECURITY


def get_kafka_servers(conf_kafka):
	'''	Retrieve the Kafka server list from the provided configuration, as the
		comma-separated string librdkafka expects for `bootstrap.servers`.

		@input conf_kafka (dict, None): the `Sonador.Kafka` configuration block

		@returns str, or None when no usable server list is configured
	'''
	servers = (conf_kafka or {}).get(SONADOR_CONF_KAFKA_SERVERS)

	if isinstance(servers, (tuple, list)):
		return ','.join(servers) or None

	# A bare string is accepted as-is: librdkafka's own format for the property is a
	# comma-separated host:port list, so an operator who writes one directly is already
	# supplying the right thing.
	return servers if isinstance(servers, str) else None


def _conf_value(conf, key):
	'''	Read a configuration value, normalizing the ways the Orthanc JSON can express
		"not set". A key that is present but null or empty is treated as absent, so that a
		template configuration carrying `"password": null` alongside a `passwordFile` does
		not read as a conflict.

		@returns the value, or None
	'''
	value = (conf or {}).get(key)
	if value is None:
		return None

	if isinstance(value, str) and not value.strip():
		return None

	return value


def _require_readable(path, conf_path):
	'''	Assert that a configured path exists and is readable by the Orthanc process.

		This is the check that turns the most common secure-transport deployment mistake --
		a certificate or secret volume that was never mounted -- from a connection which
		silently never delivers into a startup error naming the file.

		@input path (str): filesystem path read from the configuration
		@input conf_path (str): dotted configuration key, for the error message

		@raises ValueError: the path is missing, is not a regular file, or cannot be read
	'''
	if not os.path.isfile(path):
		raise ValueError('Unable to initialize Kafka connection: "%s" refers to "%s", which does not '
			'exist or is not a file. Check that the certificate/secret volume is mounted into the '
			'container.' % (conf_path, path))

	if not os.access(path, os.R_OK):
		raise ValueError('Unable to initialize Kafka connection: "%s" refers to "%s", which is not '
			'readable by the Orthanc process. Check the file permissions and ownership.'
			% (conf_path, path))

	return path


def _read_secret_file(path, conf_path):
	'''	Read a credential from a file (FR-4), so that it need not be written into the
		Orthanc JSON configuration -- which is a committed file in compose and a ConfigMap,
		not a Secret, under Kubernetes.

		Only a trailing newline is stripped. Leading and inner whitespace is significant in
		a password, and stripping it would turn a valid credential into an authentication
		failure with no diagnostic.

		@raises ValueError: the file is unreadable, or contains no credential
	'''
	_require_readable(path, conf_path)

	try:
		with open(path, 'r') as handle:
			secret = handle.read().rstrip('\r\n')

	except OSError as err:
		raise ValueError('Unable to initialize Kafka connection: unable to read "%s" from "%s": %s'
			% (conf_path, path, err))

	if not secret:
		raise ValueError('Unable to initialize Kafka connection: "%s" refers to "%s", which is empty.'
			% (conf_path, path))

	return secret


def _resolve_credential(conf, inline_key, file_key, conf_prefix):
	'''	Resolve a credential that may be supplied inline or by file reference.

		Where both forms are present the file wins, and a warning is logged naming the key
		(never the value): the file form is the one that keeps the secret out of the
		configuration, so it is the one an operator who supplied both almost certainly
		meant to take effect.

		@returns str, or None when neither form is configured
	'''
	inline = _conf_value(conf, inline_key)
	path = _conf_value(conf, file_key)

	if path is None:
		return inline

	if inline is not None:
		logger.warning('Kafka configuration supplies both "%s.%s" and "%s.%s"; the file reference '
			'takes precedence and the inline value is ignored.'
			% (conf_prefix, inline_key, conf_prefix, file_key))

	return _read_secret_file(path, '%s.%s' % (conf_prefix, file_key))


def _build_ssl_config(conf_ssl, conf_prefix):
	'''	Translate the `security.ssl` block into librdkafka `ssl.*` properties.

		@raises ValueError: a client certificate is supplied without its key or vice versa,
			or a configured path is missing or unreadable
	'''
	config = {}

	ca = _conf_value(conf_ssl, SONADOR_CONF_KAFKA_SSL_CA)
	certificate = _conf_value(conf_ssl, SONADOR_CONF_KAFKA_SSL_CERT)
	key = _conf_value(conf_ssl, SONADOR_CONF_KAFKA_SSL_KEY)

	# A client certificate and its private key are only meaningful together. Supplying one
	# without the other yields a client that negotiates TLS and is then rejected by a broker
	# requiring mutual authentication, which is a much harder failure to read than this.
	if certificate and not key:
		raise ValueError('Unable to initialize Kafka connection: "%s.%s" is configured without '
			'"%s.%s". A client certificate cannot be used without its private key.'
			% (conf_prefix, SONADOR_CONF_KAFKA_SSL_CERT, conf_prefix, SONADOR_CONF_KAFKA_SSL_KEY))

	if key and not certificate:
		raise ValueError('Unable to initialize Kafka connection: "%s.%s" is configured without '
			'"%s.%s". A private key cannot be used without its client certificate.'
			% (conf_prefix, SONADOR_CONF_KAFKA_SSL_KEY, conf_prefix, SONADOR_CONF_KAFKA_SSL_CERT))

	if ca:
		config[SONADOR_KAFKA_SSL_CA_LOCATION] = _require_readable(
			ca, '%s.%s' % (conf_prefix, SONADOR_CONF_KAFKA_SSL_CA))

	if certificate:
		config[SONADOR_KAFKA_SSL_CERTIFICATE_LOCATION] = _require_readable(
			certificate, '%s.%s' % (conf_prefix, SONADOR_CONF_KAFKA_SSL_CERT))
		config[SONADOR_KAFKA_SSL_KEY_LOCATION] = _require_readable(
			key, '%s.%s' % (conf_prefix, SONADOR_CONF_KAFKA_SSL_KEY))

	key_password = _resolve_credential(conf_ssl, SONADOR_CONF_KAFKA_SSL_KEY_PASSWORD,
		SONADOR_CONF_KAFKA_SSL_KEY_PASSWORD_FILE, conf_prefix)
	if key_password is not None:
		config[SONADOR_KAFKA_SSL_KEY_PASSWORD] = key_password

	# `verifyHostname` is emitted only when the operator states it. librdkafka already
	# defaults to verifying, and inventing the property when nothing was asked for would
	# make the constructed configuration harder to compare against the documented defaults.
	verify_hostname = (conf_ssl or {}).get(SONADOR_CONF_KAFKA_SSL_VERIFY_HOSTNAME)
	if verify_hostname is not None:
		config[SONADOR_KAFKA_SSL_ENDPOINT_ALGORITHM] = SONADOR_KAFKA_SSL_ENDPOINT_ALGORITHM_HTTPS \
			if verify_hostname else SONADOR_KAFKA_SSL_ENDPOINT_ALGORITHM_NONE

	return config


def _build_sasl_config(conf_sasl, conf_prefix):
	'''	Translate the `security.sasl` block into librdkafka `sasl.*` properties.

		@raises ValueError: the mechanism is missing or unrecognised, or a mechanism which
			authenticates with a username and password is missing either of them
	'''
	config = {}

	mechanism = _conf_value(conf_sasl, SONADOR_CONF_KAFKA_SASL_MECHANISM)
	if not mechanism:
		raise ValueError('Unable to initialize Kafka connection: "%s.%s" is required when the '
			'security protocol uses SASL. Supported mechanisms: %s.'
			% (conf_prefix, SONADOR_CONF_KAFKA_SASL_MECHANISM,
				', '.join(SONADOR_KAFKA_SASL_MECHANISM_SUPPORTED)))

	mechanism = str(mechanism).upper()
	if mechanism not in SONADOR_KAFKA_SASL_MECHANISM_SUPPORTED:
		raise ValueError('Unable to initialize Kafka connection: "%s.%s" is "%s", which is not a '
			'supported SASL mechanism. Supported mechanisms: %s.'
			% (conf_prefix, SONADOR_CONF_KAFKA_SASL_MECHANISM, mechanism,
				', '.join(SONADOR_KAFKA_SASL_MECHANISM_SUPPORTED)))

	config[SONADOR_KAFKA_SASL_MECHANISM] = mechanism

	username = _conf_value(conf_sasl, SONADOR_CONF_KAFKA_SASL_USERNAME)
	password = _resolve_credential(conf_sasl, SONADOR_CONF_KAFKA_SASL_PASSWORD,
		SONADOR_CONF_KAFKA_SASL_PASSWORD_FILE, conf_prefix)

	# GSSAPI authenticates from a Kerberos keytab and OAUTHBEARER from a token, so neither is
	# required to carry a username and password -- but if an operator supplies them they are
	# passed through rather than dropped.
	if mechanism in SONADOR_KAFKA_SASL_MECHANISM_CREDENTIALED:

		if not username:
			raise ValueError('Unable to initialize Kafka connection: "%s.%s" is required for the '
				'"%s" SASL mechanism.'
				% (conf_prefix, SONADOR_CONF_KAFKA_SASL_USERNAME, mechanism))

		if not password:
			raise ValueError('Unable to initialize Kafka connection: the "%s" SASL mechanism requires '
				'a password. Set "%s.%s" (preferred) or "%s.%s".'
				% (mechanism, conf_prefix, SONADOR_CONF_KAFKA_SASL_PASSWORD_FILE,
					conf_prefix, SONADOR_CONF_KAFKA_SASL_PASSWORD))

	if username:
		config[SONADOR_KAFKA_SASL_USERNAME] = username

	if password:
		config[SONADOR_KAFKA_SASL_PASSWORD] = password

	return config


def build_producer_config(conf_kafka):
	'''	Assemble the complete librdkafka property dict for the Sonador producer from a
		single parse of the `Sonador.Kafka` configuration block.

		With no `security` block configured the result is exactly
		`{'bootstrap.servers': <servers>}` -- the configuration the plugin has always built
		-- so an existing PLAINTEXT deployment upgrades with no configuration change.

		@input conf_kafka (dict, None): the `Sonador.Kafka` configuration block

		@returns dict: librdkafka client properties, ready to hand to `confluent_kafka.Producer`

		@raises ValueError: the server list is empty, or the security configuration cannot work
	'''
	servers = get_kafka_servers(conf_kafka)
	if not servers:
		raise ValueError('Unable to initialize Kafka connection, invalid server list')

	config = { SONADOR_KAFKA_BOOTSTRAP: servers }

	conf_security = (conf_kafka or {}).get(SONADOR_CONF_KAFKA_SECURITY)
	if not conf_security:
		return config

	conf_ssl = conf_security.get(SONADOR_CONF_KAFKA_SSL)
	conf_sasl = conf_security.get(SONADOR_CONF_KAFKA_SASL)

	# An absent protocol means the default, which is the cleartext, unauthenticated one.
	configured_protocol = _conf_value(conf_security, SONADOR_CONF_KAFKA_SECURITY_PROTOCOL)
	protocol = str(configured_protocol or SONADOR_KAFKA_PROTOCOL_PLAINTEXT).upper()

	if protocol not in SONADOR_KAFKA_PROTOCOL_SUPPORTED:
		raise ValueError('Unable to initialize Kafka connection: "%s.%s" is "%s", which is not a '
			'supported security protocol. Supported protocols: %s.'
			% (CONF_PATH_SECURITY, SONADOR_CONF_KAFKA_SECURITY_PROTOCOL, protocol,
				', '.join(SONADOR_KAFKA_PROTOCOL_SUPPORTED)))

	# Credentials configured under a protocol that cannot use them are rejected rather than
	# ignored. Silently dropping them is how a deployment ends up believing it is encrypted
	# or authenticated when it is neither -- the exact outcome this work exists to prevent.
	if conf_ssl and 'SSL' not in protocol:
		raise ValueError('Unable to initialize Kafka connection: "%s.%s" is configured, but the '
			'security protocol is "%s", which does not use TLS. Set the protocol to SSL or SASL_SSL, '
			'or remove the "%s" block.'
			% (CONF_PATH_SECURITY, SONADOR_CONF_KAFKA_SSL, protocol, SONADOR_CONF_KAFKA_SSL))

	if conf_sasl and 'SASL' not in protocol:
		raise ValueError('Unable to initialize Kafka connection: "%s.%s" is configured, but the '
			'security protocol is "%s", which does not use SASL. Set the protocol to SASL_PLAINTEXT '
			'or SASL_SSL, or remove the "%s" block.'
			% (CONF_PATH_SECURITY, SONADOR_CONF_KAFKA_SASL, protocol, SONADOR_CONF_KAFKA_SASL))

	if protocol == SONADOR_KAFKA_PROTOCOL_PLAINTEXT:

		# PLAINTEXT is librdkafka's default, so emitting the property changes nothing about
		# the connection -- but an operator who wrote it out explicitly should see it in the
		# configuration the plugin logs at startup. It is omitted only when it was omitted.
		if configured_protocol:
			config[SONADOR_KAFKA_SECURITY_PROTOCOL] = protocol

		return config

	config[SONADOR_KAFKA_SECURITY_PROTOCOL] = protocol

	if 'SSL' in protocol:
		config.update(_build_ssl_config(
			conf_ssl, '%s.%s' % (CONF_PATH_SECURITY, SONADOR_CONF_KAFKA_SSL)))

	if 'SASL' in protocol:
		config.update(_build_sasl_config(
			conf_sasl, '%s.%s' % (CONF_PATH_SECURITY, SONADOR_CONF_KAFKA_SASL)))

	return config


def redact_producer_config(config):
	'''	Return a copy of a producer configuration with every secret-bearing property masked,
		for use in log output.

		The masked value is a fixed-width placeholder rather than a length-preserving one, so
		nothing about the credential -- including its length -- is recoverable from a log.

		@input config (dict): librdkafka client properties

		@returns dict
	'''
	return { key: SONADOR_KAFKA_SECRET_REDACTED if key in SONADOR_KAFKA_SECRET_PROPERTIES else value
		for key, value in (config or {}).items() }
