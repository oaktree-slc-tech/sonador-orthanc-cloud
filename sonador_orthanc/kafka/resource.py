import json, logging, copy
import orthanc

from sonador.apisettings import DicomDatetimePairKey, \
	IMAGING_SERVER_RESOURCE_IMAGE, IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_PATIENT, \
	IMAGING_SERVER_RESOURCE_SUPPORTED, DCMHEADER_SERIES_INSTANCE_UID, DCMHEADER_STUDY_ID, DCMHEADER_PATIENT_ID, IMAGING_SERVER_UID_REGEX
from sonador.serialization import SonadorJsonEncoder

from ..apisettings import KAFKA_TIMEOUT_DEFAULT, ORTHANC_CONFIG_SECTION_SONADOR, SONADOR_CONF_KAFKA, \
	SONADOR_CONF_KAFKA_SERVERS, SONADOR_CONF_KAFKA_TOPIC, SONADOR_KAFKA_BOOTSTRAP, \
	ORTHANC_SERVER_ID as KTAG_ORTHANC_SERVER_ID, \
	ORTHANC_SERVER_RESOURCE as KTAG_ORTHANC_SERVER_RESOURCE, \
	ORTHANC_SERVER_SOURCE as KTAG_ORTHANC_SERVER_SOURCE, \
	ORTHANC_SERVER_DICOM as KTAG_ORTHANC_SERVER_DICOM

logger = logging.getLogger(__name__)


def get_study_comment_kafka_data(sonador_manager, sid, cid):
	''' Retrieve Kafka formatted data for study comment
	'''
	_iserver = sonador_manager.get_internal_imageserver()
	study = _iserver.get_study(sid)
	comment = study.get_comment(cid)
		
	
	cdata = comment.json
	cdata['Resource'] = 'Comment'
	
	return cdata


def get_study_worklist_kafka_data(sonador_manager, sid, wid):
	
	_iserver = sonador_manager.get_internal_imageserver()
	study = _iserver.get_study(sid)
	worklist_item = study.get_reviewer_worklist_item(wid)

	# Retrieve Kafka data for study
	sdata = fetch_kafka_resource_data(sonador_manager, IMAGING_SERVER_RESOURCE_STUDY, sid)
	
	wdata = worklist_item.json
	wdata['Study'] = sdata
	wdata['Resource'] = 'Worklist'
	
	return wdata


def fetch_kafka_resource_data(sonador_manager, resource_type, resource):
	'''	Retrieve the Kafka data for the provided resource
	'''
	mdata = {
		KTAG_ORTHANC_SERVER_ID: sonador_manager.imageserver_id,
		KTAG_ORTHANC_SERVER_RESOURCE: resource_type,
		'ID': resource,
	}

	# Retrieve internal imaging server
	_iserver = sonador_manager.get_internal_imageserver()

	# Patient
	if resource_type == IMAGING_SERVER_RESOURCE_PATIENT:
		rdata = _iserver.get_patient(resource)._objectdata

	# Study
	elif resource_type == IMAGING_SERVER_RESOURCE_STUDY:
		rdata = _iserver.get_study(resource)._objectdata

	# Series
	elif resource_type == IMAGING_SERVER_RESOURCE_SERIES:
		rdata = _iserver.get_series(resource)._objectdata

	# Undefined resource
	else:
		raise TypeError(
			'Unable to retrieve Kafka resource representation for resource-type="%s". Unsupported type.' % resource_type)

	# Add resource data to the message
	mdata[KTAG_ORTHANC_SERVER_DICOM] = rdata

	# Move patient, study, and series identifiers to the top-level
	if rdata.get(DCMHEADER_PATIENT_ID):
		mdata[DCMHEADER_PATIENT_ID] = rdata.get(DCMHEADER_PATIENT_ID)
	if rdata.get(DCMHEADER_STUDY_ID):
		mdata[DCMHEADER_STUDY_ID] = rdata.get(DCMHEADER_STUDY_ID)
	if rdata.get(DCMHEADER_SERIES_INSTANCE_UID):
		mdata[DCMHEADER_SERIES_INSTANCE_UID] = rdata.get(DCMHEADER_SERIES_INSTANCE_UID)

	return mdata


def kafka_instance_msg(sonador_manager, instanceId, image_data,
		instance_source=None, resource_type=IMAGING_SERVER_RESOURCE_IMAGE):
	'''	Create the structure for a Kafka instance msg. (Helper method used by both the on stored instance
		event handler and the DICOM push view.)

		@input sonador_manager: sonador server manager instance
		@input instanceId (str): UID of the DICOM instance
		@input image_data (dict): dictionary of DICOM tags for the instance
		@input instance_source: source of the image

		returns JSON dict
	'''
	mdata = {
		KTAG_ORTHANC_SERVER_ID: sonador_manager.imageserver_id,
		KTAG_ORTHANC_SERVER_RESOURCE: resource_type,
		'ID': instanceId,
		KTAG_ORTHANC_SERVER_DICOM: image_data,
	}

	# Add source of instance (if provided)
	if instance_source:
		mdata[KTAG_ORTHANC_SERVER_SOURCE] = 'DCM' if instance_source == orthanc.InstanceOrigin.DICOM_PROTOCOL \
			else 'REST' if instance_source == orthanc.InstanceOrigin.REST_API \
			else None

	# Move patient, study, and series identifiers to the top-level
	if image_data.get(DCMHEADER_PATIENT_ID):
		mdata[DCMHEADER_PATIENT_ID] = image_data.get(DCMHEADER_PATIENT_ID)
	if image_data.get(DCMHEADER_STUDY_ID):
		mdata[DCMHEADER_STUDY_ID] = image_data.get(DCMHEADER_STUDY_ID)
	if image_data.get(DCMHEADER_SERIES_INSTANCE_UID):
		mdata[DCMHEADER_SERIES_INSTANCE_UID] = image_data.get(DCMHEADER_SERIES_INSTANCE_UID)

	return mdata


def fetch_kafka_instance_data(sonador_manager, dicom, instanceId):
	'''	Retrieve the Kafka data for the provided DICOM instance
	'''
	# Create message structure
	return kafka_instance_msg(sonador_manager, instanceId, json.loads(dicom.GetInstanceSimplifiedJson()),
		instance_source=dicom.GetInstanceOrigin())


def init_export_resource_data(orthanc_config, sonador_manager):
	'''	Initialize export of Patient, Study, and Series metadata to the primary Kafka topic for
		the imaging server.
	'''
	conf_sonador = orthanc_config.get(ORTHANC_CONFIG_SECTION_SONADOR, {})
	conf_kafka = conf_sonador.get(SONADOR_CONF_KAFKA, {})

	logger.warning('Kafka: enable export of Patient, Study, and Series meta')

	if not getattr(sonador_manager, 'kafka_producer', None):
		raise ValueError('Unable to initialize resource export, manager instance does not include Kafka producer.')

	# Retrieve Kafka topic from configuration
	kafka_topic = conf_kafka.get(SONADOR_CONF_KAFKA_TOPIC)
	if not kafka_topic:
		raise ValueError('Unable to initialize Kafka connection, invalid topic')

	
	def orthanc_kafka_onstable_resource(changeType, level, resource):
		'''	Export data to Kafka about the DICOM resource marked as stable
		'''
		mdata = {
			KTAG_ORTHANC_SERVER_ID: sonador_manager.imageserver_id,
			'ID': resource,
		}

		# Patient
		if changeType == orthanc.ChangeType.STABLE_PATIENT:
			mdata = fetch_kafka_resource_data(
				sonador_manager, IMAGING_SERVER_RESOURCE_PATIENT, resource)

		# Study
		elif changeType == orthanc.ChangeType.STABLE_STUDY:
			mdata = fetch_kafka_resource_data(
				sonador_manager, IMAGING_SERVER_RESOURCE_STUDY, resource)

		# Series
		elif changeType == orthanc.ChangeType.STABLE_SERIES:
			mdata = fetch_kafka_resource_data(
				sonador_manager, IMAGING_SERVER_RESOURCE_SERIES, resource)

		# Send to Kafka
		sonador_manager.kafka_producer.send_msg(json.dumps(mdata, cls=SonadorJsonEncoder), topic=kafka_topic)


	# Stable patient, study, and series events
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.STABLE_PATIENT, orthanc_kafka_onstable_resource)
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.STABLE_STUDY, orthanc_kafka_onstable_resource)
	sonador_manager.register_serverchange_callback(
		orthanc.ChangeType.STABLE_SERIES, orthanc_kafka_onstable_resource)


def init_export_dcm(orthanc_config, sonador_manager):
	'''	Initialize export of resource DICOM data to primary Kafka topic for the imaging server.
		DICOM meta export occurs on upload to the server.
	'''
	conf_sonador = orthanc_config.get(ORTHANC_CONFIG_SECTION_SONADOR, {})
	conf_kafka = conf_sonador.get(SONADOR_CONF_KAFKA)

	logger.warning('Kafka: enable export of DICOM resource meta')

	if not getattr(sonador_manager, 'kafka_producer', None):
		raise ValueError('Unable to initialize resource export, manager instance does not include Kafka producer.')

	# Retrieve Kafka topic from configuration
	kafka_topic = conf_kafka.get(SONADOR_CONF_KAFKA_TOPIC)
	if not kafka_topic:
		raise ValueError('Unable to initialize Kafka connection, invalid topic')

	
	def orthanc_kafka_export_instance_meta(dicom, instanceId):
		'''	Export DICOM instance metadata to Kafka
		'''
		mdata = fetch_kafka_instance_data(sonador_manager, dicom, instanceId)
		orthanc.LogInfo('JSON data for DICOM instance:\n%r' % mdata)

		# Send to Kafka
		sonador_manager.kafka_producer.send_msg(json.dumps(mdata, cls=SonadorJsonEncoder), topic=kafka_topic)


	# Add export of metadata to stored instance callback chain
	sonador_manager.register_onstored_instance_callback(orthanc_kafka_export_instance_meta)