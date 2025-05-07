'''	Orthanc views to help with the management of DICOM extension models. Provides views which implement
	common REST workflows.

	* 	ObjectManagementBaseView: view class which can be used to create
		new object instances and to retrieve a list of objects.
		- POST: create new child instance
		- GET: retrieve a list of object instances
	*	ObjectBaseRestView: view class which can be used to work with a specific
		instance of an object.
		- GET: retrieve details for the instance
		- PUT: update attributes of the instance
		- DELETE: remove the instance
'''
import abc, logging, posixpath, pydicom, json, copy, datetime, traceback, uuid

from pydantic import ValidationError as PydanticValidationError

import client.apisettings as gcapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_IMAGE, DCMHEADER_SERIES_INSTANCE_UID
from sonador.serialization import SonadorJsonEncoder

from ...validation.base import OrthancViewValidationMixin

from ..base import OrthancBaseView

logger = logging.getLogger(__name__)


class ObjectViewMixin:
	'''	Mixin class which provides methods and properties for common REST API actions for
		objects within the Orthanc API.

		* data validation (uses Pydantic based "model forms")
		* serialization to JSON

		Required attributes:
		@attr sessionmaker: SQLAlchemy sessionmaker class. Normally provided via the as_view
			method of OrthancBaseView base class.
		@attr model: extension model
		@attr orthanc_objectjson (callable): function which should be used to translate
			a model instance to a JSON object.
	'''
	sonador_manager = None
	sessionmaker = None

	model = None
	model_pk_attr = 'ID'
	modelform = None

	orthanc_objectjson = None
	success_status_code = 201
	error_status_code = 400
	server_error_status_code = 500

	json_cls = SonadorJsonEncoder

	def init_object_mixin(self, *args, **kwargs):
		'''	Verify that the database properties, models, form, and object serialization functions
			have been provided to the view instance.
		'''
		self.model_pk_attr = kwargs.get('model_pk_attr', self.model_pk_attr)
		self.json_cls = kwargs.get('json_cls', self.json_cls)
		self.success_status_code = kwargs.pop('success_status_code', self.success_status_code)
		self.error_status_code = kwargs.pop('error_status_code', self.error_status_code)
		self.server_error_status_code = kwargs.pop('server_error_status_code', self.server_error_status_code)

		# Ensure that Sonador manager is present
		if not self.sonador_manager:
			raise ConfigurationError(
				'Unable to initialize %s view instance: invalid Sonador manager instance' % type(self).__name__)

		# Ensure valid session maker instance is present
		if self.sessionmaker is None:
			raise ConfigurationError(
				'Unable to initialzie %s view instance: invalid session maker instance' % type(self).__name__)

		if not callable(self.orthanc_objectjson):
			raise ConfigurationError(
				'Unable to initialize %s view instance: `orthanc_objectjson` is not a callable function.')

	def get_request_data(self, request, *args, **kwargs):
		'''	Retrieve data from the provided request
		'''
		return json.loads(request.get('body')) if request.get('body') else {}


class ObjectManagementBaseView(OrthancViewValidationMixin, ObjectViewMixin, OrthancBaseView):
	'''	Orthanc base view instance which can be used to manage objects. Includes abstract
		methods which must be implemented in a sub-class.
	'''

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Verify that the database properties, models, and serialization method have been provided.
		'''
		super().setup(output, uri, request)

		# De-serialize request data and retrieve operation parameters
		self.GET = self.request.get('get', {})
		self.POST = self.get_request_data(request, *args, **kwargs)

		# Ensure that object properties needed by the view are available
		self.init_object_mixin(*args, **kwargs)

	@abc.abstractmethod
	def get_objects(self, session, *args, **kwargs):
		'''	(Abstract Method) Retrieve objects
		'''

	@abc.abstractmethod
	def init_object_model(self, *args, **kwargs):
		'''	(Abstract Method) Initialize new model instance
		'''

	@abc.abstractmethod
	def err_404(self, err, *args, **kwargs):
		'''	(Abstract Method) Create error method to be used for a 404 message

			@returns str
		'''

	@abc.abstractmethod
	def syslog_err_validation(self, err, *args, **kwargs):
		'''	(Abstract Method) Create system log message to be used for a data validation error

			@returns str
		'''

	@abc.abstractmethod
	def syslog_exception(self, err, *args, **kwargs):
		'''	(Abstract Method): Create system log message to be used for an unknown (500) error
			during execution of a view workflow.

			@returns str
		'''
	
	def modelform_kwargs(self, *args, **kwargs):
		'''	Add keyword arguments for modelform's "clean" method
		'''
		return {}

	def get_objects_kwargs(self, *args, **kwargs):
		'''	Add keyword arguments to the get_objects method of the view.
		'''
		return {}

	def init_object_kwargs(self, *args, **kwargs):
		'''	Add keyword arguments to the init_object_model method of the view.
		'''
		return {}

	def get(self, output, uri, request, *args, **kwargs):
		''' Retrieve child models instances
		'''
		try:

			# Retrieve child objects for the provided resource
			with self.sessionmaker() as session:
				
				# Query database for child objects related to the current resource
				return self.send_response(json.dumps([
					self.orthanc_objectjson(c) for c in self.get_objects(session, **self.get_objects_kwargs(*args, session=session, **kwargs))
				], cls=self.json_cls))

		except ResourceDoesNotExist as err:			
			response = {
				gcapicodes.ERROR: self.err_404(err, *args, **kwargs),
				gcapicodes.STATUS: gcapicodes.FAIL
			}

			return self.http404_resource_not_found(response=response)

	def validate_form_data(self, session, *args, **kwargs):
		'''	Validate request data using the view model form
		'''
		return self.modelform.clean(**{
			**self.POST, **self.modelform_kwargs(session=session, create=True, **kwargs)
		})
		
	def save_object_data(self, session, form_instance, *args, **kwargs):
		'''	Save object model data
		'''
		return form_instance.save(
			session, self.init_object_model(**self.init_object_kwargs(*args, session=session, **kwargs)))

	def post(self, output, uri, request, *args, **kwargs):
		''' Create new child object for the resource
		'''	
		try:
			with self.sessionmaker() as session:

				# Create child model instance
				form_instance = self.validate_form_data(session, *args, **kwargs)
				obj = self.save_object_data(session, form_instance, *args, **kwargs)

				return self.send_response(json.dumps({
					self.model_pk_attr: obj.uid, gcapicodes.STATUS: gcapicodes.SUCCESS,
					gcapicodes.OBJECT_DATA: self.orthanc_objectjson(obj),
				}, cls=SonadorJsonEncoder), status_code=self.success_status_code)

		# Resource does not exist: 404
		except ResourceDoesNotExist as err:
			return self.http404_resource_not_found(response={
				gcapicodes.ERROR: self.err_404(err, *args, **kwargs),
				gcapicodes.STATUS: gcapicodes.FAIL
			})

		# Validation error
		except PydanticValidationError as err:
			logger.error(self.syslog_err_validation(err, *args, **kwargs))
			
			return self.send_response(
				json.dumps(self.validation_error_response(err), cls=self.json_cls),
				status_code=self.error_status_code)

		# Unknown error
		except Exception as err:
			logger.error(self.syslog_exception(err, *args, **kwargs))

			return self.send_response(json.dumps({
				'error': str(err), gcapicodes.STATUS: gcapicodes.FAIL,
			}), status_code=self.server_error_status_code)


class ObjectBaseRestView(OrthancViewValidationMixin, ObjectViewMixin, OrthancBaseView):
	'''	Orthanc view instance which acn be used to retrieve details (GET), udpate (PUT), or
		remove (DELETE) an ext object. Includes abstract methods which must be implemented in a sub-class.
	'''
	def setup(self, output, uri, request, *args, **kwargs):
		'''	Verify that the database properties, models, and serialization method have been provided.
		'''
		super().setup(output, uri, request)

		# De-serialize request data and retrieve operation parameters
		self.GET = self.request.get('get', {})
		self.POST = self.get_request_data(request, *args, **kwargs)

		# Ensure that object properties needed by the view are available
		self.init_object_mixin(*args, **kwargs)

	def get_object_uid(self, *args, sep='/', **kwargs):
		'''	Retrieve the UID of the child object from the URL
		'''
		return self.uri.split(sep)[-1]

	@abc.abstractmethod
	def get_object(self, session, *args, **kwargs):
		'''	(Abstract Method) Retrieve an object. Should throw ResourceDoesNotExist if unable to find the resource.

			@input session: active SQLalchemy session

			@returns model instance
		'''

	@abc.abstractmethod
	def err_404(self, err, *args, **kwargs):
		'''	(Abstract Method) Create error method to be used for a 404 message

			@returns str
		'''

	@abc.abstractmethod
	def syslog_err_validation(self, err, *args, **kwargs):
		'''	(Abstract Method) Create system log message to be used for a data validation error

			@returns str
		'''

	@abc.abstractmethod
	def syslog_exception(self, err, *args, **kwargs):
		'''	(Abstract Method): Create system log message to be used for an unknown (500) error
			during execution of a view workflow.

			@returns str
		'''

	def get_object_kwargs(self, *args, **kwargs):
		'''	Add keyword arguments to the get_object method of the view.
		'''
		return {}

	def _backfill_object_attrs(self, obj, attrs=None):
		'''	Generate a dictionary of object attributes that are not included in the view POST dictionary
			but are defined on the model for the purpose of back-filling validation requests.

			@returns dict: key/value mappings of attributes missing from the POST dictionary
				and required by the model form instance.
		'''
		attrs = attrs or {}

		for k in set(self.modelform.db_fieldmap.keys()).difference(self.POST.keys()):
			_v = getattr(obj, self.modelform.db_fieldmap.get(k), None)
			if _v is not None: attrs[k] = _v

		return attrs

	def modelform_kwargs(self, **kwargs):
		'''	Add keyword arguments for modelform's "clean" method
		'''
		return {}

	def get(self, output, uri, request, *args, **kwargs):
		''' Retrieve child object
		'''
		try:
			with self.sessionmaker() as session:

				# Retrieve requested child object
				return self.send_response(
					json.dumps(self.orthanc_objectjson(
						self.get_object(session, **self.get_object_kwargs(*args, session=session, **kwargs))), 
					cls=self.json_cls))

		except ResourceDoesNotExist as err:
			response = ({
				gcapicodes.ERROR: self.err_404(err, *args, **kwargs),
				gcapicodes.STATUS: gcapicodes.FAIL
			})
			return self.http404_resource_not_found(response=response)

	def update_response_json(self, obj, *args, **kwargs):
		'''	Create the JSON response structure for an update request
		'''
		return {
			self.model_pk_attr: obj.uid,
			gcapicodes.STATUS: gcapicodes.SUCCESS,
		}
		
	def validate_form_data(self, session, obj, *args, **kwargs):
		return self.modelform.clean(**{
			**self.POST, **self.modelform_kwargs(session=session, obj=obj, update=True, **kwargs)
		})
  
	def save_object_data(self, session, obj, form_instance):
		return form_instance.save(session, obj)

	def put(self, output, uri, request, *args, **kwargs):
		''' Update object instance
		'''
		try: 
			with self.sessionmaker() as session:

				# Retrieve child from database
				obj = self.get_object(session, **self.get_object_kwargs(*args, session=session, **kwargs))

				# Parse/validate request, update model properties, commit to database
				_form = self.validate_form_data(session, obj, **kwargs)
				obj = self.save_object_data(session, obj, _form)

				return self.send_response(json.dumps(
					self.update_response_json(obj, *args, **kwargs), cls=self.json_cls))

		# Validation error
		except PydanticValidationError as err:
			logger.error(self.syslog_err_validation(err, *args, **kwargs))
			
			return self.send_response(
				json.dumps(self.validation_error_response(err), cls=self.json_cls), 
				status_code=self.error_status_code)

		except ResourceDoesNotExist as err:
			response = ({
				gcapicodes.ERROR: self.err_404(err, *args, **kwargs),
				gcapicodes.STATUS: gcapicodes.FAIL
			})

			return self.http404_resource_not_found(response=response)

		except Exception as err:
			logger.error(self.syslog_exception(err, *args, update=True, **kwargs))

			return self.send_response(json.dumps({
				'error': str(err), gcapicodes.STATUS: gcapicodes.FAIL
			}, cls=self.json_cls), status_code=self.server_error_status_code)

	def delete_response_json(self, obj, *args, **kwargs):
		''' Create the JSON response structure for a delete request
		'''
		return {
			self.model_pk_attr: obj.uid,
			gcapicodes.STATUS: gcapicodes.SUCCESS,
		}

	def delete(self, output, uri, request, *args, **kwargs):
		''' Delete object instance
		'''
		try: 
			with self.sessionmaker() as session:

				# Delete object from database
				obj = self.get_object(session, self.get_object_kwargs(*args, session=session, **kwargs))
				session.delete(obj)
				session.commit()

				return self.send_response(json.dumps(
					self.delete_response_json(obj, *args, **kwargs), cls=self.json_cls))

		except ResourceDoesNotExist as err:
			response = ({
				gcapicodes.ERROR: self.err_404(err, *args, **kwargs),
				gcapicodes.STATUS: gcapicodes.FAIL
			})

			return self.http404_resource_not_found(response=response)