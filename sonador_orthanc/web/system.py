'''	Web views which provide details about the Orthanc instance, active plugins, and configuration.
'''
import json, orthanc, logging, datetime
from collections import OrderedDict

from pydicom.datadict import dictionary_keyword, dictionary_VR

import client.apisettings as gcapicodes
from client.apisettings import AUTH
from client.errors import ConfigurationError

from sonador.apisettings import DICOM_VR_DESCRIPTION, DCM_MODALITIES, DCMHEADER_MODALITY
from sonador.serialization import SonadorJsonEncoder
from sonador.helpers import dcm_tag2label

from sonador_orthanc_common.apisettings import ORTHANC_SONADOR_CONFIG_URL, ORTHANC_SONADOR_VERSION, \
	ORTHANC_CONFIG_HTTP_SERVER_SECURE, ORTHANC_CONFIG_ORTHANC_DATABASE, ORTHANC_CONFIG_ACTIVE_PLUGINS, \
	ORTHANC_CONNECTION_STATE, ORTHANC_CONNECTION_STATE_CONNECTED, ORTHANC_CONNECTION_STATE_OFFLINE, \
	ORTHANC_SONADOR_CONNECTION, ORTHANC_CONFIG_SECTION_DICT, ORTHANC_CONFIG_SECTION_SONADOR

from ..apisettings import VERSION, SONADOR_CACHE_COUNT_PATIENT, SONADOR_CACHE_COUNT_STUDY, SONADOR_CACHE_COUNT_SERIES, \
	SONADOR_CONF_PRIVATE_TAGS, SONADOR_CONF_DATETIME_TAGS, SONADOR_CONF_PRIVATE_TAGS, \
	SONADOR_CONF_KAFKA, SONADOR_CONF_KAFKA_TOPIC
from ..db.cache import CacheSeries, CacheStudy, CachePatient

from .base import OrthancBaseView

logger = logging.getLogger(__name__)


class SonadorOrthancSystemReportView(OrthancBaseView):
	'''	View instance showing Orthanc and Sonador components
	'''
	orthanc_conf = None
	servermanager = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		if not self.orthanc_conf:
			raise ConfigurationError('Unable to initialize system view, invalid Orthanc configuration.')
		if not self.servermanager:
			raise ConfigurationError('Unable to initialize system view, invalid Orthanc server manager.')

	def get(self, output, uri, request):
		'''	Retrieve Orthanc system details: plugins, config, security settings, and available DICOM tags.
			Add Sonador specific settings.			
		'''
		# Retrieve active plugins
		sys_info = json.loads(orthanc.RestApiGet('/system'))
		sys_info[ORTHANC_CONFIG_ACTIVE_PLUGINS] = json.loads(orthanc.RestApiGet('/plugins'))
		sys_info[ORTHANC_CONFIG_ORTHANC_DATABASE] = json.loads(orthanc.RestApiGet('/statistics'))

		# Check active plugins, if HttpServer marked as "insecure" and "authorization" plugin enabled,
		# modify the system report to report "secure".
		if sys_info.get(ORTHANC_CONFIG_HTTP_SERVER_SECURE) == False and AUTH in sys_info.get(ORTHANC_CONFIG_ACTIVE_PLUGINS, []):
			sys_info[ORTHANC_CONFIG_HTTP_SERVER_SECURE]= True

		# Sonador/Orthanc Version
		sys_info[ORTHANC_SONADOR_CONFIG_URL] = self.servermanager.server.url
		sys_info[ORTHANC_SONADOR_VERSION] = VERSION

		# Add private main DICOM tags to response		
		if self.orthanc_conf.get(SONADOR_CONF_PRIVATE_TAGS, {}):
			sys_info[SONADOR_CONF_PRIVATE_TAGS] = OrderedDict()

			def _private_hexcode(private_header, sep=','):
				_ptagdef = self.servermanager.tags.tag2def(private_header)
				return sep.join(_ptagdef.hex) if _ptagdef else None

			for rtype, dcm_tags in self.orthanc_conf.get(SONADOR_CONF_PRIVATE_TAGS, {}).items():
				sys_info[SONADOR_CONF_PRIVATE_TAGS][rtype] = ';'.join([pt for pt in map(_private_hexcode, dcm_tags) if pt])

		# Sonador Configuration
		sys_sonador = self.orthanc_conf.get(ORTHANC_CONFIG_SECTION_SONADOR, {})

		# Sonador/Kafka Configuration
		sys_kafka = sys_sonador.get(SONADOR_CONF_KAFKA, {})
		if sys_kafka and sys_kafka.get(SONADOR_CONF_KAFKA_TOPIC):
			sys_info['SonadorKafka'] = { 'Enabled': True, 'DcmTopic': sys_kafka.get(SONADOR_CONF_KAFKA_TOPIC) }

		return self.send_response(json.dumps(sys_info, cls=SonadorJsonEncoder))


class SonadorOrthancSystemStatusView(OrthancBaseView):
	'''	Test current status of the system: Sonador and database connection
	'''
	servermanager = None
	sessionmaker = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		if not self.servermanager:
			raise ConfigurationError('Unable to initialize status view, invalid Orther server manager.')
		if not self.sessionmaker:
			raise ConfigurationError('Unable to initialize status view, invalid session maker instance.')

	def get(self, output, uri, request, *args, **kwargs):
		''' Check database and Sonador server connection status
		'''
		response = kwargs.get('response') or { gcapicodes.OPCODE: ORTHANC_CONNECTION_STATE }

		# Check connection to Sonador
		try:
			iserver = self.servermanager.server.get_imageserver(self.servermanager.imageserver_id)
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
					% (self.servermanager.server.url, err),
				ORTHANC_SONADOR_CONFIG_URL: self.servermanager.server.url,
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
	servermanager = None
	sessionmaker = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		if not self.servermanager:
			raise ConfigurationError('Unable to initialize status view, invalid Orther server manager.')
		if not self.sessionmaker:
			raise ConfigurationError('Unable to initialize status view, invalid session maker instance.')

	def dcm_tagdata(self, dcmtag, sep=','):
		'''	Retrieve data for the provided tag
		'''
		_tag = self.servermanager.tags.code2def(dcmtag)

		
		# Tag details
		_tagdef =  {
			'code': ','.join(_tag.hex), 'tag': _tag.header, 'label': dcm_tag2label(_tag.header), 
			'private': _tag.private, 'vr': { 'code':  _tag.dtype },
		}

		# Add VR type
		if DICOM_VR_DESCRIPTION.get(_tag.dtype):
			_tagdef['vr']['name'] = DICOM_VR_DESCRIPTION.get(_tag.dtype).name

		# Add options
		if DCMTAG_OPTIONS.get(_tag.header):
			_tagdef['options'] = DCMTAG_OPTIONS.get(_tag.header)

		return _tagdef

	def get(self, output, uri, request, *args, **kwargs):
		'''	Retrieve system configuration and create VR map of available tags.
		'''
		# Retrieve tags configuredin the main DICOM tags cache
		sconfig = self.servermanager.get_internal_imageserver().system_info()
		dcmtags = sconfig.get('MainDicomTags', {})

		for rtype,rtags in dcmtags.items():
			dcmtags[rtype] = dict((dcmtag, self.dcm_tagdata(dcmtag)) for dcmtag in rtags.split(';'))

		private_dcmtags = sconfig.get(SONADOR_CONF_PRIVATE_TAGS, {})
		for rtype, rtags in private_dcmtags.items():
			for dcmcode in rtags.split(';'):

				# Retrieve tag definition and add to the response
				_ptagdef = self.dcm_tagdata(dcmcode)
				if _ptagdef:
					dcmtags[rtype][dcmcode]	= _ptagdef

		return self.send_response(json.dumps(dcmtags, cls=SonadorJsonEncoder))