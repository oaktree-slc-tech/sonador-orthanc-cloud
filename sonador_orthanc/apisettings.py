from sonador_orthanc_common.apisettings import *


# Sonador/Orthanc Plugin Version
VERSION = '0.3-beta3'

# Sonador Cache Index Status Codes
SONADOR_CACHE_STATUS_CURRENT = 'current'
SONADOR_CACHE_STATUS_INCOMPLETE = 'incomplete'

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

# Sonador Cache Response Codes
SONADOR_CACHE_COUNT_PATIENT = 'patient-count'
SONADOR_CACHE_COUNT_STUDY = 'study-count'
SONADOR_CACHE_COUNT_SERIES = 'series-count'


# Private DICOM Tags
SONADOR_CONF_PRIVATE_TAGS = 'PrivateMainDicomTags'
SONADOR_CONF_DATETIME_TAGS = 'DicomExtDatetime'


# Query Request Components
SONADOR_CACHE_ORDER_BY = 'OrderBy'
