'''	Orthanc views allowing for the triggering of server change events in Orthanc.
'''
import posixpath, logging, json, copy, datetime
import orthanc

import client.apisettings as gcapicodes
import client.utils.apisettings as gcuapicodes
from client.errors import ConfigurationError
from client.utils.conversion import str2bool
from client.utils.object import omit, pick

from .base import OrthancBaseView

logger = logging.getLogger(__name__)


class OrthancEventView(OrthancBaseView):
	'''	Orthanc view instance able to translate REST requests into server-side change events.
		Used by the Sonador plugin internally to register on delete and on update events
		for patients, studies, and series.
			
		DELETE: trigger "OnDelete" for resource type
		PUT: trigger "OnUpdate" for resource type
	'''
	servermanager = None
	update_event_type = None
	delete_event_type = None
	resource_class = None

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Setup view instance to ensure that server manager and event types are present.
		'''
		super().setup(output, uri, request)

		# Ensure that Sonador manager instance is present
		if self.servermanager is None:
			raise ConfigurationError('Unable to initialize %s view: invalid Sondor manager instance' % type(self).__name__)

		# Ensure that event types and resource class are defined
		if self.update_event_type is None or self.delete_event_type is None or self.resource_class is None:
			raise ConfigurationError('Unable to initialize %s view: invalid event type or resource class')

	def put(self, output, uri, request):
		'''	Trigger update event for resource
		'''
		# Parse resource UID from URI
		base_uri, ruid = posixpath.split(uri)

		# Trigger server manager event
		logger.debug('trigger update for resource-type=%s uid="%s"' % (self.resource_class, ruid))
		self.servermanager.trigger_serverchange_callback(self.update_event_type, self.resource_class, ruid)

		self.send_response(json.dumps({
			gcapicodes.OPCODE: 'trigger-event', 'type': self.update_event_type,
			'resource': self.resource_class,  'uid': ruid,
			gcapicodes.STATUS: gcapicodes.SUCCESS,
		}))

	def delete(self, output, uri, request):
		''' Trigger delete event
		'''
		# Parse UID from URI
		base_uri, ruid = posixpath.split(uri)

		# Trigger server manager
		logger.debug('trigger delete event for resource-type=%s uid="%s"' % (self.resource_class, ruid))
		self.servermanager.trigger_serverchange_callback(self.delete_event_type, self.resource_class, ruid)

		self.send_response(json.dumps({
			gcapicodes.OPCODE: 'trigger-event', 'type': self.delete_event_type,
			'resource': self.resource_class, 'uid': ruid,
			gcapicodes.STATUS: gcapicodes.SUCCESS,
		}))