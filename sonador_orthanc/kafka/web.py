'''	Orthanc views which allow for triggering export of data to Kafka
'''
import posixpath, logging, json, copy, datetime, traceback
import orthanc

import client.apisettings as gcapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist

import client.utils.apisettings as gcuapicodes
from client.utils.conversion import str2bool
from client.utils.object import omit, pick

from sonador.apisettings import IMAGING_SERVER_RESOURCE_IMAGE
from sonador.serialization import SonadorJsonEncoder

from ..apisettings import SONADOR_KAFKA_REQUEST_DATA
from ..db.internal import Resource, ORTHANCDB_INSTANCE_TYPE
from ..web.base import OrthancBaseView
from ..web.cache import ResourceUidMixin
from ..web.secure_user import UserContextMixin

from .base import KafkaMixin
from .resource import fetch_kafka_resource_data, kafka_instance_msg

logger = logging.getLogger(__name__)


class OrthancKafkaExportView(KafkaMixin, UserContextMixin, ResourceUidMixin, OrthancBaseView):
	'''	Orthanc view instance able to export resource Kafka data to the DICOM instances topic.

		* GET: retrieve a copy of the resource data
		* POST: trigger export to Kafka

		@attr sessionmaker (database session maker class)
	'''
	sessionmaker = None
	kafka_opcode = None
	server_error_status_code = 500

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Verify that all components of the request
		'''
		super().setup(output, uri, request, *args, **kwargs)

		# Initialize Kafka mixin attributes
		self._init_kafka(self, *args, **kwargs)

		# Ensure that an operation code was added to the view

		# DICOM push configured to pull from internal resource model rather than Sonador cache.
		# View will only return Kafka data for instances. Patients, studies, and series queries
		# will return a 404.
		if self.resource_cachemodel == Resource:
			logger.warning(('%s view configured to utilize %s model for both resource and cache resource queries. '
				+ 'View instance will only return responses for DICOM instances.') % (type(self).__name__, self.resource_cachemodel.__name__))
			self.resource_code = ORTHANCDB_INSTANCE_TYPE
			self.resource_type = IMAGING_SERVER_RESOURCE_IMAGE
			
		# Verify properties of the resource cache model
		else:
			self.init_resource_mixin(*args, **kwargs)

		# Parse POST parameters from request body to attach to the exported Kafka message
		self.POST = json.loads(request.get('body')) if request.get('body') else {}

	def syslog_exception(self, err, *args, **kwargs):
		'''	Create system log error for exception
		'''
		return 'Unable to execute Kafka operation for resource-type="%s" resource-uid="%s" due to an error. Error: "%s"\n%s' % (
			self.resource_cachemodel.__name__, self.get_resource_uid(*args, **kwargs), err, traceback.format_exc()
		)

	def get_object(self, session, *args, **kwargs):
		'''	Retrieve object instance: performs a lookup query against the database
			to ensure that the UID parsed from the URL is correct. Raises DoesNotExist
			if the method is unable to locate a matching instances. (Delegates to get_resource.)
		'''
		return self.get_resource(session, *args, **kwargs)		

	def fetch_kafka_data(self, output, uri, request, *args, **kwargs):
		'''	Retrieve resource, aggregate data, and prepare data to send to Kafka
		'''
		# Retrieve resource
		with self.sessionmaker() as session:
			obj = self.get_object(session, *args, **kwargs)

			# Retrieve Kafka data for the object
			if self.resource_cachemodel != Resource and hasattr(self.resource_cachemodel, 'type'):
				_kafka = fetch_kafka_resource_data(
					self.sonador_manager, self.resource_cachemodel.type, obj.publicid)

			# Retrieve DCM details for instance
			elif self.resource_cachemodel == Resource:
				_iserver = self.sonador_manager.get_internal_imageserver()

				# Retrieve DICOM instance and create Kafka message
				dcm = _iserver.get_dcm_instance(obj.publicid)
				_kafka = kafka_instance_msg(self.sonador_manager, dcm.pk, dcm.tags)

			else:
				raise NotImplementedError('Unsupported resource type: %s' % self.resource_cachemodel.__name__)
			
			return _kafka

	def get(self, output, uri, request, *args, **kwargs):
		'''	Retrieve resource Kafka data and return in response body
		'''
		try:

			# Retrieve Kafka data for the resource
			_kafka = self.fetch_kafka_data(output, uri, request, *args, **kwargs)	
			_kafka[gcapicodes.STATUS] = gcapicodes.SUCCESS

			return self.send_response(json.dumps(_kafka, cls=self.json_cls))

		except ResourceDoesNotExist as err:
			response = {
				gcapicodes.ERROR: self.err_404(err, *args, **kwargs),
				gcapicodes.STATUS: gcapicodes.FAIL,
			}

			return self.http404_resource_not_found(response=response)

		# Unknown error
		except Exception as err:
			logger.error(self.syslog_exception(err, *args, **kwargs))

			return self.send_response(json.dumps({
				'error': str(err), gcapicodes.STATUS: gcapicodes.FAIL,
			}, cls=self.json_cls), status_code=self.server_error_status_code)

	def post(self, output, uri, request, *args, **kwargs):
		'''	Retrieve 
		'''
		try:

			# Retrieve Kafka data for the resource
			_kafka = self.send_kafka_msg(output, uri, request, *args, **kwargs)

			# Attach request payload (if present)
			if self.POST:
				_kafka[SONADOR_KAFKA_REQUEST_DATA] = self.POST

			# Attach details of the user who triggered the data export
			self.init_user_context(request, *args, **kwargs)
			_kafka['User'] = self.user._objectdata

			# Add operation status of push
			_kafka[gcapicodes.OPCODE] = self.kafka_opcode
			_kafka[gcapicodes.STATUS] = gcapicodes.SUCCESS

			return self.send_response(json.dumps(_kafka, cls=self.json_cls))

		except ResourceDoesNotExist as err:

			response = {
				gcapicodes.ERROR: self.err_404(err, *args, **kwargs),
				gcapicodes.OPCODE: self.kafka_opcode,
				gcapicodes.STATUS: gcapicodes.FAIL,
			}

			return self.http404_resource_not_found(response)

		# Unknown error
		except Exception as err:
			logger.error(self.syslog_exception(err, *args, **kwargs))

			return self.send_response(json.dumps({
				'error': str(err), gcapicodes.STATUS: gcapicodes.FAIL,
			}, cls=self.json_cls), status_code=self.server_error_status_code)