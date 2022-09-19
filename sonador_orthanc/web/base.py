from abc import ABC
import asyncio, logging, json

from client.utils.decorators import classproperty
from client import apisettings as gcapicodes

from sonador.serialization import SonadorJsonEncoder

logger = logging.getLogger(__name__)


class OrthancBaseView(ABC):
	''' Class which can be used to process Orthanc web requests
	'''
	http_method_names = [
		'get',
		'post',
		'put',
		'patch',
		'delete',
		'head',
		'options',
		'trace'
	]

	http404_error_default = 'Resource does not exist'

	def __init__(self, *args, **kwargs):
		'''	Constructor method called at the time when a view is registered. Provides 
			the ability for the view to set extra keyword arguments and other parameters.
		'''
		for key,value in kwargs.items():
			setattr(self, key, value)

	@classproperty
	def view_is_async(cls):
		handlers = [
			getattr(cls, method)
			for method in cls.http_method_names
			if (method != "options" and hasattr(cls, method))
		]
		if not handlers:
			return False
		is_async = asyncio.iscoroutinefunction(handlers[0])
		if not all(asyncio.iscoroutinefunction(h) == is_async for h in handlers[1:]):
			raise ImproperlyConfigured(
				f"{cls.__qualname__} HTTP handlers must either be all sync or all "
				"async."
			)
		return is_async

	@classmethod
	def as_view(cls, **initkwargs):
		'''	Main entry point for a request/response process.
		'''
		for key in initkwargs:
			for key in initkwargs:
				if key in cls.http_method_names:
					raise TypeError(
						"The method name %s is not accepted as a keyword argument "
						"to %s()." % (key, cls.__name__)
					)
				if not hasattr(cls, key):
					raise TypeError(
						"%s() received an invalid keyword %r. as_view "
						"only accepts arguments that are already "
						"attributes of the class." % (cls.__name__, key)
					)

		def view(output, uri, **request):
			self = cls(**initkwargs)
			self.setup(output, uri, request)
			if not hasattr(self, 'request'):
				raise AttributeError(('%s instance has not "request" attribute. Did you override '
					+ 'setup() and forget to call super()?') % __cls.__name__)

			return self.dispatch(output, uri, request)

		view.view_class = cls
		view.view_initkwargs = initkwargs

		# __name__ and __qualname__ are intentionally left unchanged as view_class should
		# be used to robustly determine the name of the view instead.
		view.__doc__ = cls.__doc__
		view.__module__ = cls.__module__
		view.__annotations__ = cls.dispatch.__annotations__

		# Copy possible attributes set by decorators from the dispatch method.
		view.__dict__.update(cls.dispatch.__dict__)

		# Mark the callback if the view class is async
		if cls.view_is_async:
			view._is_coroutine = asyncio._is_coroutine

		return view

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Initialize attributes shared by all view methods.
		'''
		if hasattr(self, 'get') and not hasattr(self, 'head'):
			self.head = self.get
		
		self.uri = uri	
		self.request = request
		self.output = output
		self.method = self.request.get('method', '')
		self.body = self.request.get('body')

	def dispatch(self, output, uri, request):
		'''	Determine the correct method for the view. If a method doesn't exist
			defer to an error handler. Also defer to the error handler if the request method
			isn't on the approved list.
		'''
		if self.method.lower() in self.http_method_names:
			handler = getattr(
				self, self.method.lower(), self.http_method_not_allowed)
		else:
			handler = self.http_method_not_allowed

		return handler(output, uri, request)

	def send_response(self, response, status_code=None, headers=None, mtype='application/json'):
		'''	Write JSON response and status code to output
		'''
		headers = headers or {}
		for hkey,hval in headers.items():
			self.output.SetHttpHeader(hkey,hval)

		# Send status code
		if status_code:
			self.output.SetHttpHeader('Content-Type', mtype)
			self.output.SendHttpStatus(status_code, response, len(response))

		# Send answer buffer with respones and mime-type
		else:
			self.output.AnswerBuffer(response, mtype)

	def _allowed_methods(self):
		return [m.upper() for m in self.http_method_names if hasattr(self, m)]

	def http_method_not_allowed(self, *args, **kwargs):
		'''	Send response indicating that the HTTP method used to access the view is not allowed.
		'''
		logger.warning(
			'Method not allowed (%s): %s' % (self.method, self.uri))
		return self.output.SendMethodNotAllowed(self.method.upper())

	def http404_resource_not_found(self, *args, **kwargs):
		'''	Send response indicating that the resource associated with the view could not be located.
		'''
		response = kwargs.get('response', {})
		if not response.get(gcapicodes.ERROR):
			response[gcapicodes.ERROR] = kwargs.get('message', self.http404_error_default)

		return self.send_response(
			json.dumps(response, cls=SonadorJsonEncoder), 
			status_code=kwargs.get('status_code', 404), *args, **kwargs)

	def options(self, output, uri, request):
		'''	Handle OPTIONS requests
		'''
		return self.send_response('', status_code=204, headers={
			'Allow': ', '.join(self._allowed_methods()),			
		}, mtype='text/plain')

