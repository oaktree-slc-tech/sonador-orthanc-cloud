import posixpath

from sonador.apisettings.worklists import SONADOR_WORKLIST_STATUS_SCHEDULED, \
	SONADOR_WORKLIST_STATUS_INPROGRESS, SONADOR_WORKLIST_STATUS_COMPLETED, \
	SONADOR_WORKLIST_STATUS_CANCELLED
from sonador_orthanc_common.apisettings import *

from client import apisettings as gapi


# Sonador/Orthanc Plugin Version
VERSION = '0.4-rc1'

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