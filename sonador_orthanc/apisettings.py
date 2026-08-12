import posixpath

from sonador.apisettings.worklists import SONADOR_WORKLIST_STATUS_SCHEDULED, \
	SONADOR_WORKLIST_STATUS_INPROGRESS, SONADOR_WORKLIST_STATUS_COMPLETED, \
	SONADOR_WORKLIST_STATUS_CANCELLED
from sonador_orthanc_common.apisettings import *

from client import apisettings as gapi


# Sonador/Orthanc Plugin Version. Reported to clients as the `SonadorVersion` key of the
# `/system` endpoint (see `sonador_orthanc.web.system.SonadorOrthancSystemReportView`), alongside
# `SonadorUrl`; the Sonador viewer surfaces it in its About table as the "Imaging Server Sonador
# Cloud Plugin Version".
VERSION = '0.4-dev'

# Sonador Cache Index Status Codes
SONADOR_CACHE_STATUS_CURRENT = 'current'
SONADOR_CACHE_STATUS_INCOMPLETE = 'incomplete'

# Sonador Cache Endpoints
SONADOR_CACHE_URL_ROOT = '/cache'
SONADOR_CACHE_TAGS_URL = posixpath.join(SONADOR_CACHE_URL_ROOT, 'dcm-tags')

# Sonador Event Types
SONADOR_RESOURCE_UPDATE_PATIENT = 'sonador-patient-update'
SONADOR_RESOURCE_UPDATE_STUDY = 'sonador-study-update'
SONADOR_RESOURCE_UPDATE_SERIES = 'sonador-series-update'
SONADOR_RESOURCE_DELETE_PATIENT = 'sonador-patient-delete'
SONADOR_RESOURCE_DELETE_STUDY = 'sonador-study-delete'
SONADOR_RESOURCE_DELETE_SERIES = 'sonador-series-delete'

# Sonador Cache Index Operations
SONADOR_CACHE_OPCODE_INDEX_RESOURCES = 'index-resources'
SONADOR_CACHE_OPCODE_INDEX_PATIENT = 'index-patient'
SONADOR_CACHE_OPCODE_INDEX_DELETE_PATIENT = 'index-delete-patient'
SONADOR_CACHE_OPCODE_INDEX_STUDY = 'index-study'
SONADOR_CACHE_OPCODE_INDEX_DELETE_STUDY = 'index-delete-study'
SONADOR_CACHE_OPCODE_INDEX_SERIES = 'index-series'
SONADOR_CACHE_OPCODE_INDEX_DELETE_SERIES = 'index-delete-series'
SONADOR_CACHE_OPCODE_INDEX_IMAGE = 'index-instance'
SONADOR_CACHE_OPCODE_INDEX_DELETE_IMAGE = 'index-delete-instance'

# Sonador Cache Response Codes
SONADOR_CACHE_COUNT_PATIENT = 'patient-count'
SONADOR_CACHE_COUNT_STUDY = 'study-count'
SONADOR_CACHE_COUNT_SERIES = 'series-count'
SONADOR_CACHE_COUNT_INSTANCES = 'instances-count'


# Private DICOM Tags
SONADOR_CONF_PRIVATE_TAGS = 'PrivateMainDicomTags'
SONADOR_CONF_DATETIME_TAGS = 'DicomExtDatetime'


# Cache Settings
SONADOR_CONF_CACHE = 'Cache'
SONADOR_CONF_CACHE_THREADS_COUNT = 'CacheThreadsCount'


# Query Request Components
SONADOR_CACHE_ORDER_BY = 'OrderBy'
REQUIRE_EXPLICIT_ACCESS_QUERY_PARAM = 'requireExplicitAccess'



# Sonador Error Codes
SONAODR_OBJECT_DUPLICATE_ERROR = gapi.VALIDATION_APICODE_DUPLICATE
SONAODR_OBJECT_INVALID_ERROR = gapi.VALIDATION_APICODE_INVALID



# Sonador Model Attributes
SONADOR_USER_ATTRS_DEFAULT = ('id', 'username', 'email', 'first_name', 'last_name')
SONADOR_GROUP_ATTRS_DEFAULT = ('id', 'name')
SONADOR_ACL_ATTRS_DEFAULT = ('view', 'modify', 'remove', 'acl', 'comment_edit', 'comment_view')



# Access Control List Policy Types
AUTH_POLICY_TYPE_USER = 'user'
AUTH_POLICY_TYPE_GROUP = 'group'

AUTH_POLICY_TYPE_SUPPORTED = (AUTH_POLICY_TYPE_USER, AUTH_POLICY_TYPE_GROUP)



# Kafka Settings and Default Values
KAFKA_TIMEOUT_DEFAULT = 0
SONADOR_CONF_KAFKA = 'Kafka'
SONADOR_CONF_KAFKA_SERVERS = 'servers'
SONADOR_CONF_KAFKA_TOPIC = 'topic'
SONADOR_KAFKA_BOOTSTRAP = 'bootstrap.servers'

SONADOR_KAFKA_REQUEST_DATA = 'RequestData'


# Kafka Delivery Retry Policy
#
# librdkafka has already exhausted its own `message.send.max.retries` by the time a delivery
# report arrives with an error, so the application-level retry in
# `sonador_orthanc.kafka.base.SonadorProducer.delivery_report` is a last resort. It is bounded
# and backed off so that a broker which is rejecting the client outright -- an expired
# certificate or a rotated SASL credential, both of which only become possible once the
# transport is secured -- cannot turn into a hot re-produce loop.
KAFKA_DELIVERY_MAX_ATTEMPTS = 3
KAFKA_DELIVERY_RETRY_BACKOFF = 2.0


# Kafka Transport Security -- Orthanc Configuration Keys
#
# These name the keys of the optional `Sonador.Kafka.security` block. They are lowerCamelCase
# to match the `topic` and `servers` keys already in that block, and are deliberately NOT the
# librdkafka property names: the mapping between the two is stated once, in
# SONADOR_KAFKA_SECURITY_PROPERTY_MAP below, so it can be checked against the upstream
# librdkafka CONFIGURATION.md without reading any logic.
SONADOR_CONF_KAFKA_SECURITY = 'security'
SONADOR_CONF_KAFKA_SECURITY_PROTOCOL = 'protocol'

SONADOR_CONF_KAFKA_SSL = 'ssl'
SONADOR_CONF_KAFKA_SSL_CA = 'ca'
SONADOR_CONF_KAFKA_SSL_CERT = 'certificate'
SONADOR_CONF_KAFKA_SSL_KEY = 'key'
SONADOR_CONF_KAFKA_SSL_KEY_PASSWORD = 'keyPassword'
SONADOR_CONF_KAFKA_SSL_KEY_PASSWORD_FILE = 'keyPasswordFile'
SONADOR_CONF_KAFKA_SSL_VERIFY_HOSTNAME = 'verifyHostname'

SONADOR_CONF_KAFKA_SASL = 'sasl'
SONADOR_CONF_KAFKA_SASL_MECHANISM = 'mechanism'
SONADOR_CONF_KAFKA_SASL_USERNAME = 'username'
SONADOR_CONF_KAFKA_SASL_PASSWORD = 'password'
SONADOR_CONF_KAFKA_SASL_PASSWORD_FILE = 'passwordFile'


# Kafka Transport Security -- librdkafka Property Names
#
# Every name below is a documented librdkafka client property. Nothing here is invented: see
# CONFIGURATION.md in confluent/librdkafka.
SONADOR_KAFKA_SECURITY_PROTOCOL = 'security.protocol'
SONADOR_KAFKA_SSL_CA_LOCATION = 'ssl.ca.location'
SONADOR_KAFKA_SSL_CERTIFICATE_LOCATION = 'ssl.certificate.location'
SONADOR_KAFKA_SSL_KEY_LOCATION = 'ssl.key.location'
SONADOR_KAFKA_SSL_KEY_PASSWORD = 'ssl.key.password'
SONADOR_KAFKA_SSL_ENDPOINT_ALGORITHM = 'ssl.endpoint.identification.algorithm'
SONADOR_KAFKA_SASL_MECHANISM = 'sasl.mechanism'
SONADOR_KAFKA_SASL_USERNAME = 'sasl.username'
SONADOR_KAFKA_SASL_PASSWORD = 'sasl.password'

# Values for `ssl.endpoint.identification.algorithm`. librdkafka spells "verify the broker
# certificate against the hostname we connected to" as the HTTPS identification algorithm, and
# "do not" as none; there is no boolean form of the property.
SONADOR_KAFKA_SSL_ENDPOINT_ALGORITHM_HTTPS = 'https'
SONADOR_KAFKA_SSL_ENDPOINT_ALGORITHM_NONE = 'none'

# Configuration key -> librdkafka property. `verifyHostname` is absent because it is a boolean
# that maps onto a value rather than onto a property name; it is handled explicitly by the
# builder. This map exists to be read, not iterated -- the builder assembles properties in a
# fixed order so validation can be specific about which key is at fault.
SONADOR_KAFKA_SECURITY_PROPERTY_MAP = {
	SONADOR_CONF_KAFKA_SECURITY_PROTOCOL: SONADOR_KAFKA_SECURITY_PROTOCOL,
	SONADOR_CONF_KAFKA_SSL_CA: SONADOR_KAFKA_SSL_CA_LOCATION,
	SONADOR_CONF_KAFKA_SSL_CERT: SONADOR_KAFKA_SSL_CERTIFICATE_LOCATION,
	SONADOR_CONF_KAFKA_SSL_KEY: SONADOR_KAFKA_SSL_KEY_LOCATION,
	SONADOR_CONF_KAFKA_SSL_KEY_PASSWORD: SONADOR_KAFKA_SSL_KEY_PASSWORD,
	SONADOR_CONF_KAFKA_SASL_MECHANISM: SONADOR_KAFKA_SASL_MECHANISM,
	SONADOR_CONF_KAFKA_SASL_USERNAME: SONADOR_KAFKA_SASL_USERNAME,
	SONADOR_CONF_KAFKA_SASL_PASSWORD: SONADOR_KAFKA_SASL_PASSWORD,
}


# Kafka Transport Security -- Supported Values
SONADOR_KAFKA_PROTOCOL_PLAINTEXT = 'PLAINTEXT'
SONADOR_KAFKA_PROTOCOL_SSL = 'SSL'
SONADOR_KAFKA_PROTOCOL_SASL_PLAINTEXT = 'SASL_PLAINTEXT'
SONADOR_KAFKA_PROTOCOL_SASL_SSL = 'SASL_SSL'

SONADOR_KAFKA_PROTOCOL_SUPPORTED = (SONADOR_KAFKA_PROTOCOL_PLAINTEXT, SONADOR_KAFKA_PROTOCOL_SSL,
	SONADOR_KAFKA_PROTOCOL_SASL_PLAINTEXT, SONADOR_KAFKA_PROTOCOL_SASL_SSL)

SONADOR_KAFKA_SASL_MECHANISM_PLAIN = 'PLAIN'
SONADOR_KAFKA_SASL_MECHANISM_SCRAM_SHA_256 = 'SCRAM-SHA-256'
SONADOR_KAFKA_SASL_MECHANISM_SCRAM_SHA_512 = 'SCRAM-SHA-512'
SONADOR_KAFKA_SASL_MECHANISM_GSSAPI = 'GSSAPI'
SONADOR_KAFKA_SASL_MECHANISM_OAUTHBEARER = 'OAUTHBEARER'

SONADOR_KAFKA_SASL_MECHANISM_SUPPORTED = (SONADOR_KAFKA_SASL_MECHANISM_PLAIN,
	SONADOR_KAFKA_SASL_MECHANISM_SCRAM_SHA_256, SONADOR_KAFKA_SASL_MECHANISM_SCRAM_SHA_512,
	SONADOR_KAFKA_SASL_MECHANISM_GSSAPI, SONADOR_KAFKA_SASL_MECHANISM_OAUTHBEARER)

# Mechanisms that authenticate with a username and password supplied by the client. GSSAPI
# authenticates from a Kerberos keytab and OAUTHBEARER from a token, so neither is required to
# carry `sasl.username` / `sasl.password` and neither is checked for them.
SONADOR_KAFKA_SASL_MECHANISM_CREDENTIALED = (SONADOR_KAFKA_SASL_MECHANISM_PLAIN,
	SONADOR_KAFKA_SASL_MECHANISM_SCRAM_SHA_256, SONADOR_KAFKA_SASL_MECHANISM_SCRAM_SHA_512)


# Producer properties whose values are secret and must never reach a log line, at any level.
# `sonador_orthanc.kafka.helpers.redact_producer_config` is the only sanctioned way to render a
# producer configuration for logging.
SONADOR_KAFKA_SECRET_PROPERTIES = frozenset((
	SONADOR_KAFKA_SSL_KEY_PASSWORD,
	SONADOR_KAFKA_SASL_PASSWORD,
))

SONADOR_KAFKA_SECRET_REDACTED = '********'


# Kafka Push Operations
SONADOR_KAFKA_OPCODE_PUSH_PATIENT = 'kafka-export.patient'
SONADOR_KAFKA_OPCODE_PUSH_STUDY = 'kafka-export.study'
SONADOR_KAFKA_OPCODE_PUSH_SERIES = 'kafka-export.series'
SONADOR_KAFKA_OPCODE_PUSH_IMAGE = 'kafka-export.instance'
SONADOR_KAFKA_OPCODE_PUSH_WORKLIST = 'kafka-export.study-worklist'
SONADOR_KAFKA_OPCODE_PUSH_STUDY_COMMENT = 'kafka-export.study-comment'
SONADOR_KAFKA_OPCODE_PUSH_SERIES_COMMENT = 'kafka-export.series-comment'



# Worklist Virtual Status Codes
SONADOR_WORKLIST_VIRTUAL_STATUS_OPEN = 'Open'
SONADOR_WORKLIST_VIRTUAL_STATUS_ALL = 'All'
SONADOR_WORKLIST_STATUS_SUPPORTED = (
	SONADOR_WORKLIST_STATUS_SCHEDULED, 
	SONADOR_WORKLIST_STATUS_INPROGRESS,
	SONADOR_WORKLIST_STATUS_COMPLETED,
	SONADOR_WORKLIST_STATUS_CANCELLED
)
SONADOR_WORKLIST_STATUS_SUPPORTED_LOWERCASE = dict([(s.lower(), s) for s in SONADOR_WORKLIST_STATUS_SUPPORTED])



# Distortion Filter
DISTORTION_FILTER_INDEX = 'DistortionFilterIndex'
DISTORTION_FILTER_DEVICE_UID = 'DeviceUID'
DISTORTION_FILTER_DEVICE_MODEL = 'DeviceModel'
DISTORTION_FILTER_RESULT = 'Result'
DISTORTION_FILTER_ERROR = 'Error'
DISTORTION_FILTER_MESSAGE = 'Message'
DISTORTION_FILTER_RESULT_IGNORE = 'Ignore'
DISTORTION_FILTER_RESULT_APPLIED = 'Filter Applied'
DISTORTION_FILTER_RESULT_NOT_APPLIED = 'Filter Not Applied'
