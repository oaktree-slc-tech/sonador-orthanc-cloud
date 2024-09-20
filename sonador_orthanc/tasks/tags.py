import json, logging, uuid, traceback
import orthanc

from sonador.apisettings import DicomDatetimePairKey, \
	IMAGING_SERVER_RESOURCE_IMAGE, IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_PATIENT, \
	IMAGING_SERVER_RESOURCE_SUPPORTED, DCMHEADER_SERIES_INSTANCE_UID, DCMHEADER_STUDY_ID, DCMHEADER_PATIENT_ID, IMAGING_SERVER_UID_REGEX, \
	DCMHEADER_MODALITY, DCM_MODALITY_SR, DCMHEADER_SR_PERTINENT_OTHER_EVIDENCE_SEQUENCE, DCMHEADER_SR_REF_SERIES_SEQ, \
	DCMHEADER_SR_CONTENT_SEQUENCE

from ..apisettings import KAFKA_TIMEOUT_DEFAULT, ORTHANC_CONFIG_SECTION_SONADOR, SONADOR_CONF_KAFKA, \
	SONADOR_CONF_KAFKA_SERVERS, SONADOR_CONF_KAFKA_TOPIC, SONADOR_KAFKA_BOOTSTRAP, \
	ORTHANC_SERVER_ID as KTAG_ORTHANC_SERVER_ID, \
	ORTHANC_SERVER_RESOURCE as KTAG_ORTHANC_SERVER_RESOURCE, \
	ORTHANC_SERVER_SOURCE as KTAG_ORTHANC_SERVER_SOURCE, \
	ORTHANC_SERVER_DICOM as KTAG_ORTHANC_SERVER_DICOM

from ..db.comments import ImagingSeriesComment
from ..validation.comments import CommentValidationForm

logger = logging.getLogger(__name__)

COMMENT_TEMPLATE_DEFAULT = 'DICOM-SR Meta: SR-ID=%(ID)s, SR-DCMUID=%(SeriesInstanceUID)s)'


def init_dcmsr_comment_parsing(orthanc_config, sonador_manager, sessionmaker, comment_template=COMMENT_TEMPLATE_DEFAULT):
	''' Initialize parsing of DICOM-SR documents. DICOM-SR instances are scanned for 
		the series they reference, and the contents are attached as comment metadata.
	'''
	logger.warning('Tags: enable parsing of DICOM-SR data to series comment meta')


	def orthanc_dcmsr_comment_parsing(dicom, instanceId):
		'''	Parse DICOM-SR instances to JSON and attach annotations to the referenced series
		'''
		iserver = sonador_manager.get_internal_imageserver()
		idata = json.loads(dicom.GetInstanceSimplifiedJson())

		if idata.get(DCMHEADER_MODALITY) == DCM_MODALITY_SR:

			# Retrieve SR data from top-level headers
			sr_data = idata.get(DCMHEADER_SR_CONTENT_SEQUENCE)
			if sr_data:

				# Iterate through referenced series
				for _ref_data in idata.get(DCMHEADER_SR_PERTINENT_OTHER_EVIDENCE_SEQUENCE, []):
					for _ref_sx in _ref_data.get(DCMHEADER_SR_REF_SERIES_SEQ, []):

						# Retrieve series to create comment
						if _ref_sx.get(DCMHEADER_SERIES_INSTANCE_UID):
							_results = iserver.query_series({
								DCMHEADER_SERIES_INSTANCE_UID: _ref_sx.get(DCMHEADER_SERIES_INSTANCE_UID)
							}, rapid_lookup=False, request_kwargs={ 'core_api': True })

							if len(_results): _sx = _results[0]
							else: _sx = None

							if _sx:

								def create_sx_dcmsr_comment():
									'''	Copy comment data to referenced series
									'''
									try:
										# Retrieve Orthanc system user
										r_sysuser = sonador_manager.server.admin_verify_user_credentials(
											sonador_manager.server.apitoken_type, sonador_manager.server.apitoken)
										sysuser = sonador_manager.server._init_dataclass(
											sonador_manager.server.user_datacollection_class.model, r_sysuser.get('user', {}))

										with sessionmaker() as session:

											# Check series comment to determine if the data from the DICOM-SR has
											# already been persisted.
											_comments = _sx.fetch_comments()
											if any((instanceId in _c.text or instanceId == _c.meta.get('SR-ID')) for _c in _comments):

												logger.warning('Content from DICOM-SR instance=%s already associated with series=%s. Indexing skipped.' % (
													instanceId, _sx.pk
												))

											else:

												comment = CommentValidationForm.clean(**{
													'Text': comment_template % { **idata, 'ID': instanceId },
													'Meta': {
														'SR-ID': instanceId, 'SR-DCMUID': idata.get(DCMHEADER_SERIES_INSTANCE_UID), 'SR-DCM': sr_data, 
													},
													'request_user': sysuser, 'create': True, 'session': session, 
												}).save(session, ImagingSeriesComment(**{
													'uid': str(uuid.uuid4()), 
													ImagingSeriesComment.resource_foreignkey_attr: _sx.pk,
													'user': sysuser.pk,
												}))

									except Exception as err:
										logger.error('Unable to index DICOM-SR instance=%s due to an error. Error: "%s".\n%s'
										% (instanceId, err, traceback.format_exc()))

								
								# Submit create callback to background queue
								try: sonador_manager.threadpool.submit(create_sx_dcmsr_comment)
								except Exception as err:
									logger.error('Unable to index DICOM-SR instance=%s due to an error. Error: "%s".\n%s'
										% (instanceId, err, traceback.format_exc()))


	# Add parsing of metadata to stored instance callback chain
	sonador_manager.register_onstored_instance_callback(orthanc_dcmsr_comment_parsing)