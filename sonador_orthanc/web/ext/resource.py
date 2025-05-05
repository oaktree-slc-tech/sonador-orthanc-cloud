''' Orthanc views to help with the management of DICOM extension models associated with a parent resource.
	View instances inherit from ext.base.ObjectManagementBaseView and ext.base.ObjectBaseRestView.

	* 	ResourceChildManagementBaseView: view class which can be used to create
		new child object instances and to retrieve a list of child objects associated
		with a specific resource.
		- POST: create new child instance
		- GET: retrieve a list of child instances associated with a specific parent
	*	ResourceChildBaseRestView: view class which can be used to work with a specific
		instance of a child object.
		- GET: retrieve details for the child instance
		- PUT: update attributes of the child
		- DELETE: remove the child instance

	The views of this module delegate data validation and persistence to Pydantic "form"
	classes. Refer to the sonador_orthanc.validation module for more detail.
'''
import abc, logging, posixpath, pydicom, json, copy, datetime, traceback, uuid

from pydantic import ValidationError as PydanticValidationError

import client.apisettings as gcapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist
from client.utils.object import pick, omit

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_IMAGE, DCMHEADER_SERIES_INSTANCE_UID
from sonador.serialization import SonadorJsonEncoder

from ...cache.web import ResourceUidMixin

from ..base import OrthancBaseView

from .base import ObjectViewMixin, ObjectManagementBaseView, ObjectBaseRestView

logger = logging.getLogger(__name__)


class ResourceChildMixin(ResourceUidMixin, ObjectViewMixin):
	'''	Mixin class which provides methods and properites for common REST API actions for
		child objects of an Orthanc resource (patient, study, series) including:
		
		* retrieving parent models from the Sonador/Orthanc resource cache
		* data validation (uses Pydantic based "model forms")
		* serialization to JSON

		Inherits from ext.base.ObjectViewMixin.

		Required attributes:
		
		@attr resource_cachemodel: resource cache model class
	'''
	resource_cachemodel = None

	def init_resource_mixin(self, *args, **kwargs):
		'''	Verify that the database properties, models, form, and object serialization function
			have been provided to the view instance.
		'''
		self.init_object_mixin(*args, **kwargs)
		super().init_resource_mixin(*args, **kwargs)


class ResourceChildManagementBaseView(ResourceChildMixin, ObjectManagementBaseView):
	''' Orthanc view instance which can be used to manage objects which are the child
		of a resource (patient, study, series).
	'''
	server_error_status_code = 400

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Verify that the database properties, models, and serialization method have been provided.
		'''
		super().setup(output, uri, request)

		# De-serialize request data and retrieve operation parameters
		self.POST = json.loads(request.get('body')) if request.get('body') else {}

		# Ensure that a resource model has been defined and an index method is available
		self.init_resource_mixin(*args, **kwargs)

	def get_objects_kwargs(self, *args, **kwargs):
		'''	Retrieve keyword arguments for "get_objects". Invokes get_resource to trigger a 404
			if the parent resource does not exist.
			
			@returns dict
			* `rid`: UID of the parent resource
			* `ruid`: UID of the parent resource
			* `resource`: resource model of the parent
		'''
		# Retrieve parent resource UID from the URL
		rid = self.get_resource_uid(*args, **kwargs)

		# Ensure that a session was provided		
		session = kwargs.get('session')
		if not session:
			raise ValueError('Unable to retrieve resource model, no session provided')

		# Retrieve resource from database. Triggers DoesNotExist if the resource does not exist.
		r = self.get_resource(session, *args, ruid=rid, **omit(kwargs, ('session',)))

		return {
			'rid': rid, 'ruid': r.publicid, 'resource': r
		}

	def init_object_kwargs(self, *args, **kwargs):
		'''	Retrieve keyword arguments for init_object_model. Invokes get_resource to trigger a 404
			if the parent resource does not exist. (Delegates to get_objects_kwargs.)
		'''
		return self.get_objects_kwargs(*args, **kwargs)

	def err_404(self, err, *args, **kwargs):
		'''	Create 404 error message which includes the UID of the parent resource
		'''
		rid = kwargs.get('rid') or kwargs.get('ruid') or self.get_resource_uid(*args, **kwargs)
		return 'Resource %s=%s does not exist' % (self.resource_cachemodel.type, rid or '(none)')

	def syslog_err_validation(self, err, *args, **kwargs):
		'''	Create system log validation error
		'''
		return 'Unable to create child object "%s" due to a form validation error. Error:\n%s' % (
			self.model.__name__, err
		)

	def syslog_exception(self, err, *args, **kwargs):
		'''	Create system log error for exception
		'''
		'Unable to create child object "%s" due to an error. Error: "%s"\n%s' % (
			self.model.__name__, err, traceback.format_exc()
		)


class ResourceChildBaseRestView(ResourceChildMixin, ObjectBaseRestView):
	'''	REST endpoint which can be used to retrieve details (GET), update (PUT), or remove (DELETE) a
		resource child/ext object.
	'''
	server_error_status_code = 400

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Verify that the database properties, models, and serialization method have been provided.
		'''
		super().setup(output, uri, request)

		# De-serialize request data and retrieve operation parameters
		self.POST = json.loads(request.get('body')) if request.get('body') else {}

		# Ensure that a resource model has been defined and a serialization method is available
		self.init_resource_mixin(*args, **kwargs)

	@abc.abstractmethod
	def get_object(self, session, *args, rid=None, cid=None, **kwargs):
		'''	(Abstract Method) Retrieve a child object for the provided resource ID. Should throw ResourceDoesNotExist
			if unable to find either the parent resoruce a child object with the provided UID.

			@input session: active SQLalchemy session
			@input rid (str): parent resource UID
			@input cid (str): child resource UID

			@returns child model instance
		'''

	def get_object_kwargs(self, *args, **kwargs):
		'''	Add keyword arguments to the get_object method of the view.
		'''
		return {
			'rid': kwargs.get('rid') or kwargs.get('ruid') or self.get_resource_uid(*args, **kwargs),
			'cid': kwargs.get('cid') or self.get_object_uid(*args, **kwargs),
		}

	def err_404(self, err, *args, **kwargs):
		'''	Create 404 error message which includes the UID of the parent resource
		'''
		rid = self.get_resource_uid(*args, **kwargs)
		cid = self.get_object_uid(*args, **kwargs)

		return 'Child ID=%s for %s=%s does not exist' % (
			cid or '(none)', self.resource_cachemodel.type, rid or '(none)'
		)

	def update_response_json(self, obj, *args, **kwargs):
		'''	Create the JSON response structure for an update request
		'''
		# Create response data structure, add parent UID
		rdata = super().update_response_json(obj, *args, **kwargs)
		rdata[self.resource_cachemodel.type] = self.get_resource_uid(*args, **kwargs)

		return rdata

	def delete_response_json(self, obj, *args, **kwargs):
		''' Create the JSON response structure for a delete request
		'''
		# Create response data structure, add parent UID
		rdata = super().delete_response_json(obj, *args, **kwargs)
		rdata[self.resource_cachemodel.type] = self.get_resource_uid(*args, **kwargs)

		return rdata

	def syslog_err_validation(self, err, *args, **kwargs):
		'''	Create system log validation error
		'''
		return 'Unable to update child object "%s" due to a form validation error. Error:\n%s' % (
			self.model.__name__, err
		)

	def syslog_exception(self, err, *args, **kwargs):
		'''	Create system exception error
		'''
		if kwargs.get('update'):

			# Retrieve resource and object UID from URL
			getobj_kwargs = self.get_object_kwargs(*args, **kwargs)
			cid = kwargs.get('cid')
			rid = kwargs.get('rid')

			return 'Unable to update %s=%s %s=%s due to error. Error: %s\n%s' % (
				self.resource_cachemodel.type, rid, self.model.type, cid, err, traceback.format_exc()
			)

		raise ValueError('Unable to template syslog error, unknown request type')
		