import six, os, json, logging, pprint, threading, requests, traceback, posixpath
from typing import List
from concurrent.futures import ThreadPoolExecutor as ThreadPool

from client import apisettings as capicodes
from client.errors import ConfigurationError, ResourceDoesNotExist
from client.utils.object import pick

from sonador.servers import SonadorServer, SonadorImagingServer
from sonador.servers.auth import SonadorGroupAccessControlListCollection
from sonador.remote import sonador_dataobject_create

from sonador_orthanc_common import apisettings as orthancapi
from sonador_orthanc_common.manager import BaseServerManager, \
	TIMER_30S, TIMER_MINUTE, TIMER_10MIN, TIMER_10MIN, TIMER_30MIN, \
	TIMER_HOUR, TIMER_DAILY
from sonador_orthanc_common.servers import ResponseLikeObject, OrthancInternalImagingServer

from .apisettings import SONADOR_CONF_KAFKA

logger = logging.getLogger(__name__)



IMAGE_SERVER_CONFIG_TRANSFORMS = {
	orthancapi.ORTHANC_SONADOR_CONFIG_SERVER_SCHEME: 'scheme',
	orthancapi.ORTHANC_SONADOR_CONFIG_SERVER_HOSTNAME:  'hostname',
	orthancapi.ORTHANC_SONADOR_CONFIG_SERVER_PORT: 'port',
	orthancapi.ORTHANC_SONADOR_CONFIG_SERVER_NAME: 'name',
	orthancapi.ORTHANC_SONADOR_CONFIG_SERVER_DESCRIPTION:  'description',
	orthancapi.ORTHANC_SONADOR_CONFIG_SERVER_INTERNAL_SCHEME: 'internal_scheme',
	orthancapi.ORTHANC_SONADOR_CONFIG_SERVER_INTERNAL_HOSTNAME: 'internal_hostname',
	orthancapi.ORTHANC_SONADOR_CONFIG_SERVER_INTERNAL_PORT: 'internal_port',
	orthancapi.ORTHANC_SONADOR_SERVER_ACTIVE: 'active',
}


class OrthancCloudInternalImagingServer(OrthancInternalImagingServer):
	'''	Imaging server implementaiton which can be used within the Sonador/Orthanc cloud plugin.
		Uses the orthanc Python package to retrieve and populate data structures and perform user/group.
	'''
	group_acl_datacollection_class = SonadorGroupAccessControlListCollection

	def admin_verify_user_credentials(self, token_key, token_value, **kwargs):
		'''	Verify the provided token key and value using the imaging server introspection endpoint.
			If valid, a copy of the user context will be provided for the server including the profile,
			and the groups that have been authorized for the imaging server instance.

			@returns response-like object
		'''
		return SonadorImagingServer.admin_verify_user_credentials(self, token_key, token_value, **kwargs)

	def user_lookup(self, user_uids: List[int], **kwargs):
		'''	Retrieve the details of the users specified in user_uids
		'''
		return SonadorImagingServer.user_lookup(self, user_uids, **kwargs)

	def group_lookup(self, group_uids: List[int], **kwargs):
		'''	Retrieve the details of the groups specified in group_uids
		'''
		return SonadorImagingServer.group_lookup(self, group_uids, **kwargs)

	def fetch_acl(self, **kwargs):
		'''	Retrieve ACL policies associated with the server
		'''
		return SonadorImagingServer.fetch_acl(self, **kwargs)


class SonadorServerManager(BaseServerManager):
	'''	Manages the integration between Sonador and Orthanc and provides methods for
		scheduling recurring tasks, executing long-running operations, and invoking callbacks
		on server changes.
	'''
	registration_delay = 30
	retry_limit = 3
	retry_interval = 30

	def __init__(self, sonador_conn: SonadorServer, imageserver_id: str, *args, 
			threadpool=None, timers=None, changeCallbacks=None, kafka_producer=None, **kwargs):
		'''	Initialize server manager
		'''
		self.imageserver_id = imageserver_id
		self.retry_limit = kwargs.get('retry_limit', self.retry_limit)
		self.retry_interval = kwargs.get('retry_interval', self.retry_interval)
		self.registration_delay = kwargs.get('registration_delay', self.registration_delay)

		super().__init__(
			sonador_conn, *args, threadpool=threadpool, timers=timers, changeCallbacks=changeCallbacks, **kwargs)

		# Kafka producer
		self.kafka_producer = kafka_producer

	def register_server(self, *args, **kwargs):
		''' Synchronize local server configuration with remote configuration on Sonador. If an entry
			does not exit exist, it will be created.
		'''
		# Retry registration up to limit
		retry = kwargs.get('retry', 0)
		if retry < self.retry_limit:

			try:

				# Transform configuration to database schema
				sdata = { 'uid': self.imageserver_id }
				for ckey in IMAGE_SERVER_CONFIG_TRANSFORMS:
					if self.conf.get(ckey):
						sdata[IMAGE_SERVER_CONFIG_TRANSFORMS.get(ckey)] = self.conf.get(ckey)

				# Retrieve and update image server entry
				try:
					iserver = self.server.get_imageserver(self.imageserver_id)
					iserver = iserver.update(sdata)

				# Create server entry if it does not exist
				except ResourceDoesNotExist as err:
					rdata = sonador_dataobject_create(self.server, SonadorImagingServer, sdata, verify=self.server.verify)

					# Ensure that the Sonador assigned server ID matches the local server ID
					if rdata.get(capicodes.UPDATE_URL):

						# Parse server assigned ID from update URL, retrieve instance to ensure
						# it was created correctly within Sonador database and compare UID to the local UID.
						_, iserver_uid = posixpath.split(rdata.get(capicodes.UPDATE_URL))
						iserver = self.serrver.get_imageserver(iserver_uid)
						assert self.imageserver_id == iserver.pk

				logger.warning('Orthanc instance %s registered with Sonador successfully' % self.imageserver_id)
				return

			# Queue retry 
			except Exception as err:
				logger.critical('Unable to register Orthanc instance %s with Sonador. Retry (%s/%s) in %s seconds'
					% (self.imageserver_id, retry+1, self.retry_limit, self.retry_interval))

				# Retry registration of the server in 30 seconds
				self.create_scheduled_task(30, lambda: self.register_server(retry=retry+1))

		# Unable to register server with Sonador: stop Orthanc
		else:
			logger.critical('Unable to register Orthanc instance %s with Sonador (failed %s/%s attempts).'
				% (self.imageserver_id, retry, self.retry_limit))
			self.shutdown_orthanc()

	def get_internal_imageserver(self, *args, **kwargs):
		'''	Retrieve an image server instance that can be used by Orthanc plugins.
		'''
		iserver_data = kwargs.get('iserver_data', { OrthancInternalImagingServer.pk_attr: self.imageserver_id, })
		if self.conf.get(orthancapi.ORTHANC_SONADOR_CONFIG_SERVER_NAME):
			iserver_data['name'] = self.conf.get(orthancapi.ORTHANC_SONADOR_CONFIG_SERVER_NAME)

		return self.server.get_imageserver(self.imageserver_id, imageserver_datamodel_class=OrthancCloudInternalImagingServer,
			fetch_callable=lambda *_a, **_ka: ResponseLikeObject(json.dumps(iserver_data)))
