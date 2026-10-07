'''	Web views which provide details about the Orthanc instance, active plugins, and configuration.
'''
import json, orthanc, logging, datetime
from collections import OrderedDict

from pydicom.datadict import dictionary_keyword, dictionary_VR

import client.apisettings as gcapicodes
from client.apisettings import AUTH
from client.errors import ConfigurationError
from client.utils.object import pick, omit

from sonador.apisettings import DICOM_VR_DESCRIPTION, DCM_MODALITIES, DCMHEADER_MODALITY
from sonador.serialization import SonadorJsonEncoder
from sonador.helpers import dcm_tag2label

from sonador_orthanc_common.apisettings import ORTHANC_SONADOR_CONFIG_URL, ORTHANC_SONADOR_VERSION, \
	ORTHANC_CONFIG_HTTP_SERVER_SECURE, ORTHANC_CONFIG_ORTHANC_DATABASE, ORTHANC_CONFIG_ACTIVE_PLUGINS, \
	ORTHANC_CONNECTION_STATE, ORTHANC_CONNECTION_STATE_CONNECTED, ORTHANC_CONNECTION_STATE_OFFLINE, \
	ORTHANC_SONADOR_CONNECTION, ORTHANC_CONFIG_SECTION_DICT, ORTHANC_CONFIG_SECTION_SONADOR

from ..apisettings import VERSION, SONADOR_USER_ATTRS_DEFAULT, SONADOR_CACHE_COUNT_PATIENT, SONADOR_CACHE_COUNT_STUDY, SONADOR_CACHE_COUNT_SERIES, \
	SONADOR_CONF_PRIVATE_TAGS, SONADOR_CONF_DATETIME_TAGS, SONADOR_CONF_PRIVATE_TAGS, \
	SONADOR_CONF_KAFKA, SONADOR_CONF_KAFKA_TOPIC
from ..db.cache import CacheSeries, CacheStudy, CachePatient

from .base import OrthancBaseView
from .secure_user import UserContextMixin

logger = logging.getLogger(__name__)


class SonadorOrthancSystemReportView(UserContextMixin, OrthancBaseView):
	'''	View instance showing Orthanc and Sonador components
	'''
	orthanc_conf = None
	sonador_manager = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		if not self.orthanc_conf:
			raise ConfigurationError('Unable to initialize system view, invalid Orthanc configuration.')
		if not self.sonador_manager:
			raise ConfigurationError('Unable to initialize system view, invalid Orthanc server manager.')

	def get(self, output, uri, request, *args, **kwargs):
		'''	Retrieve Orthanc system details: plugins, config, security settings, and available DICOM tags.
			Add Sonador specific settings.			
		'''
		# Retrieve user details to filter system details: requests without headers are Orthanc internal
		# requests and retrieving the user context should be skipped.
		if request.get('headers'):
			self.init_user_context(request, *args, **kwargs)

		# Retrieve active plugins
		sys_info = json.loads(orthanc.RestApiGet('/system'))
		sys_info[ORTHANC_CONFIG_ACTIVE_PLUGINS] = json.loads(orthanc.RestApiGet('/plugins'))
		sys_info[ORTHANC_CONFIG_ORTHANC_DATABASE] = json.loads(orthanc.RestApiGet('/statistics'))

		# Check active plugins, if HttpServer marked as "insecure" and "authorization" plugin enabled,
		# modify the system report to report "secure".
		if sys_info.get(ORTHANC_CONFIG_HTTP_SERVER_SECURE) == False and AUTH in sys_info.get(ORTHANC_CONFIG_ACTIVE_PLUGINS, []):
			sys_info[ORTHANC_CONFIG_HTTP_SERVER_SECURE]= True

		# Sonador/Orthanc Version
		sys_info[ORTHANC_SONADOR_CONFIG_URL] = self.sonador_manager.server.url
		sys_info[ORTHANC_SONADOR_VERSION] = VERSION

		# Add private main DICOM tags to response		
		if self.orthanc_conf.get(SONADOR_CONF_PRIVATE_TAGS, {}):
			sys_info[SONADOR_CONF_PRIVATE_TAGS] = OrderedDict()

			def _private_hexcode(private_header, sep=','):
				_ptagdef = self.sonador_manager.tags.tag2def(private_header)
				return sep.join(_ptagdef.hex) if _ptagdef else None

			for rtype, dcm_tags in self.orthanc_conf.get(SONADOR_CONF_PRIVATE_TAGS, {}).items():
				sys_info[SONADOR_CONF_PRIVATE_TAGS][rtype] = ';'.join([pt for pt in map(_private_hexcode, dcm_tags) if pt])

		# Sonador Configuration
		sys_sonador = self.orthanc_conf.get(ORTHANC_CONFIG_SECTION_SONADOR, {})

		# Sonador/Kafka Configuration
		sys_kafka = sys_sonador.get(SONADOR_CONF_KAFKA, {})
		if sys_kafka and sys_kafka.get(SONADOR_CONF_KAFKA_TOPIC):
			sys_info['SonadorKafka'] = { 'Enabled': True, 'DcmTopic': sys_kafka.get(SONADOR_CONF_KAFKA_TOPIC) }

		# Identity of the request user, so clients can tell which comments and other user-owned
		# objects belong to the signed-in user.
		if getattr(self, 'user', None):
			sys_info['User'] = pick(self.user, SONADOR_USER_ATTRS_DEFAULT)

		# Filter sensitive system details
		if getattr(self, 'user', None) and not self.user.is_superuser:
			sys_info = omit(sys_info, ('OrthancDatabase', 'UserMetadata', 'HttpPort', 'DicomAet',
				'MaximumPatientCount', 'MaximumStorageMode', 'MaximumStorageSize', 'name', 'OverwriteInstances',
				'PluginsEnabled', 'ReadOnly', 'StorageAreaPlugin', 'SonadorUrl', 'SonadorKafka'))

		return self.send_response(json.dumps(sys_info, cls=SonadorJsonEncoder))


class SonadorOrthancSystemStatusView(OrthancBaseView):
	'''	Test current status of the system: Sonador and database connection
	'''
	sonador_manager = None
	sessionmaker = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		if not self.sonador_manager:
			raise ConfigurationError('Unable to initialize status view, invalid Orther server manager.')
		if not self.sessionmaker:
			raise ConfigurationError('Unable to initialize status view, invalid session maker instance.')

	def get(self, output, uri, request, *args, **kwargs):
		''' Check database and Sonador server connection status
		'''
		response = kwargs.get('response') or { gcapicodes.OPCODE: ORTHANC_CONNECTION_STATE }

		# Check connection to Sonador
		try:
			iserver = self.sonador_manager.server.get_imageserver(self.sonador_manager.imageserver_id)
			response[ORTHANC_SONADOR_CONNECTION] = {
				gcapicodes.OPRESULT: gcapicodes.SUCCESS,
				'ts': datetime.datetime.utcnow(),
				gcapicodes.STATUS: ORTHANC_CONNECTION_STATE_CONNECTED,
			}

		# Notify user that the gateway is offline
		except Exception as err:
			response[ORTHANC_SONADOR_CONNECTION] = {
				gcapicodes.OPRESULT: gcapicodes.FAIL,
				'ts': datetime.datetime.utcnow(),
				gcapicodes.STATUS: ORTHANC_CONNECTION_STATE_OFFLINE,
				gcapicodes.ERROR: 'Unable to connect to Sonador instance "%s" due to an error:\n%s'
					% (self.sonador_manager.server.url, err),
				ORTHANC_SONADOR_CONFIG_URL: self.sonador_manager.server.url,
			}

		# Check connection to database
		try:

			# Count number of patients, studies, and series in the Sonador resource cache
			with self.sessionmaker() as session:
				response[ORTHANC_CONFIG_ORTHANC_DATABASE] = {
					gcapicodes.OPRESULT: gcapicodes.SUCCESS,
					'ts': datetime.datetime.utcnow(),
					SONADOR_CACHE_COUNT_PATIENT: session.query(CachePatient).count(),
					SONADOR_CACHE_COUNT_STUDY: session.query(CacheStudy).count(),
					SONADOR_CACHE_COUNT_SERIES: session.query(CacheSeries).count(),
					gcapicodes.STATUS: ORTHANC_CONNECTION_STATE_CONNECTED,
				}

		# Notify user that database is offline
		except Exception as err:
			response[ORTHANC_CONFIG_ORTHANC_DATABASE] = {
				gcapicodes.OPRESULT: gcapicodes.FAIL,
				'ts': datetime.datetime.utcnow(),
				gcapicodes.STATUS: ORTHANC_CONNECTION_STATE_OFFLINE,
				gcapicodes.ERROR: 'Unable to connect to Orthanc database due to an error:\n%a' % err,
			}

		# Set response status code: 200 if all components online, 500 otherwise
		if response.get(ORTHANC_SONADOR_CONNECTION, {}).get(gcapicodes.STATUS) == ORTHANC_CONNECTION_STATE_CONNECTED \
			and response.get(ORTHANC_CONFIG_ORTHANC_DATABASE, {}).get(gcapicodes.STATUS) == ORTHANC_CONNECTION_STATE_CONNECTED:
			status_code = 200
		else: status_code = 500

		return self.send_response(json.dumps(response, cls=SonadorJsonEncoder), status_code=status_code)


DCMTAG_OPTIONS = {
	DCMHEADER_MODALITY: DCM_MODALITIES,
}


def dcm_tagdata(sonador_manager, dcmtag):
	'''	Catalogue entry for one tag code: `{ code, tag, label, private, vr: { code, name? }, options? }`,
		or None when the server's tag dictionary does not know the code.
	'''
	_tag = sonador_manager.tags.code2def(dcmtag)
	if _tag is None:
		return None

	_tagdef = {
		'code': ','.join(_tag.hex), 'tag': _tag.header, 'label': dcm_tag2label(_tag.header),
		'private': _tag.private, 'vr': { 'code': _tag.dtype },
	}

	if DICOM_VR_DESCRIPTION.get(_tag.dtype):
		_tagdef['vr']['name'] = DICOM_VR_DESCRIPTION.get(_tag.dtype).name

	if DCMTAG_OPTIONS.get(_tag.header):
		_tagdef['options'] = DCMTAG_OPTIONS.get(_tag.header)

	return _tagdef


def build_dcmtag_catalogue(sonador_manager):
	'''	The tag catalogue served by `/cache/dcm-tags`: `{ Level: { 'GGGG,EEEE': tagdef } }` from the
		server's MainDicomTags and PrivateMainDicomTags. Unknown or empty codes are skipped.
	'''
	sconfig = sonador_manager.get_internal_imageserver().system_info()
	dcmtags = {}

	for rtype, rtags in (sconfig.get('MainDicomTags') or {}).items():
		dcmtags[rtype] = {}
		for dcmcode in (rtags or '').split(';'):
			_tagdef = dcm_tagdata(sonador_manager, dcmcode) if dcmcode.strip() else None
			if _tagdef:
				dcmtags[rtype][dcmcode] = _tagdef
			elif dcmcode.strip():
				logger.warning('Skipping unknown %s main DICOM tag "%s"' % (rtype, dcmcode))

	for rtype, rtags in (sconfig.get(SONADOR_CONF_PRIVATE_TAGS) or {}).items():
		for dcmcode in (rtags or '').split(';'):
			_tagdef = dcm_tagdata(sonador_manager, dcmcode) if dcmcode.strip() else None
			if _tagdef:
				dcmtags.setdefault(rtype, {})[dcmcode] = _tagdef
			elif dcmcode.strip():
				logger.warning('Skipping unknown %s private DICOM tag "%s"' % (rtype, dcmcode))

	return dcmtags


def catalogue_tagdef(catalogue, code):
	'''	The catalogue entry whose code matches `code` (canonical `GGGG,EEEE`), at any level
	'''
	code = (code or '').upper()
	for level in (catalogue or {}).values():
		for key, tagdef in (level or {}).items():
			if (tagdef.get('code') or key or '').upper() == code:
				return tagdef

	return None


class SonadorOrthancDicomTagsView(OrthancBaseView):
	'''	Retrieve the list of DICOM tags and value representations currently configured for the Orthanc server.
		Tag data is split by the resource type (Patient, Study, Series, Instance) and keyed to the DICOM hexadecimal code.

		Response components:
		*	Resource level: `Patient`, `Study`, `Series`, `Instance`
		*	Resource components:
			-	code (key): DICOM hexadecimal code for the resource
			-	resource:
				+	tag: short name of the DICOM tag
				+	name: long name of the tag
				+	vr: value representation of the resource
					@	code: DICOM VR code (example: LO)
					@	description: description of the value representation (example: "Long String")

		Sample response:

		```json
		{
		  Patient: {
		    '0010,0020': {
		      'tag': 'PatientID',
		      'name': 'Patient ID',
		      'vr': {
		        'code': 'LO',
		        'description': 'Long String',
		      }
		    }
		  },
		  Study: {
		    ...
		  },
		  Series: {
		    ...
		  },
		  Instance: {
		    ...
		  }
		}

		```
	'''
	sonador_manager = None
	sessionmaker = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		if not self.sonador_manager:
			raise ConfigurationError('Unable to initialize status view, invalid Orther server manager.')
		if not self.sessionmaker:
			raise ConfigurationError('Unable to initialize status view, invalid session maker instance.')

	def dcm_tagdata(self, dcmtag, sep=','):
		'''	Retrieve data for the provided tag
		'''
		return dcm_tagdata(self.sonador_manager, dcmtag)

	def get(self, output, uri, request, *args, **kwargs):
		'''	Retrieve system configuration and create VR map of available tags.
		'''
		return self.send_response(json.dumps(build_dcmtag_catalogue(self.sonador_manager), cls=SonadorJsonEncoder))