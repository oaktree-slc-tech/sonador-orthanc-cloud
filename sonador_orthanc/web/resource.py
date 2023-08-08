import abc, json, posixpath, logging, traceback
import orthanc

import client.apisettings as gcapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist

from sonador.serialization import SonadorJsonEncoder

from sonador_orthanc_common.servers import ResponseLikeObject, local_orthanc_apiurl

from .base import OrthancBaseView
from .cache import ResourceBaseMixin

logger = logging.getLogger(__name__)


class SonadorResourceBaseView(ResourceBaseMixin, OrthancBaseView):
	''' View instance which can be used to work with Orthanc resources.
	'''
	resource_base = None
	sessionmaker = None
	resource_cachemodel = None

	def setup(self, output, uri, request, *args, **kwargs):
		''' Parse request options
		'''
		if not self.resource_base:
			raise ConfigurationError('Unable to initialize view %s, invalid resource endpoint' % type(self).__name__)
		if not self.resource_cachemodel:
			raise ConfigurationError('Unable to initialize view %s, invalid resource cache model' % type(self).__name__)
		if not self.sessionmaker:
			raise ConfigurationError('Unable to initialize view %s, invalid session maker instance' % type(self).__name__)

		request = request or {}
		super().setup(output, uri, request, *args, **kwargs)
		self.init_resource_mixin(*args, 
			resource_type=self.resource_cachemodel.type, resource_code=self.resource_cachemodel.code, **kwargs)

		# Retrieve URL query parameters from request
		self.GET = self.request.get('get', {})

	def orthanc_resource_json(self, session, resource, *args, response=None, **kwargs):
		'''	Retrieve JSON data for the provided patient instance
		'''
		response = response or {}

		# Retrieve patient JSON
		r = ResponseLikeObject(orthanc.RestApiGet(
			local_orthanc_apiurl(posixpath.join(self.resource_base, resource.publicid), query_params=self.GET)))

		# Add patient data and private tags to response
		response.update(r.json())
		rp = session.query(self.resource_cachemodel.privatetags_resource_model).get(resource.publicid)
		if rp: response['MainDicomTags'].update(rp.orthanc)
		
		return response

	def delete_resource(self, session, resource, *args, response=None, **kwargs):
		'''	Delete resource instance
		'''
		response = response or {}

		# Delete patient resource
		r = ResponseLikeObject(orthanc.RestApiDelete(
			local_orthanc_apiurl(posixpath.join(self.resource_base, resource.publicid), query_params=self.GET)))
		if r.text:
			response.update(r.json())

		return response

	def _execute_resource_request(self, callable, output, uri, request, *args, 
			esmg_404='Resource uid=%s does not exist', emsg_500='Server error (uid=%s). Error: "%s".', **kwargs):
		'''	Attempt to execute the provided resource request.
		'''
		response = kwargs.get('response') or {}

		try:
			with self.sessionmaker() as session:
				r = self.get_resource(session, *args, **kwargs)
				return self.send_response(
					json.dumps(callable(session, r, *args, response=response, **kwargs), cls=SonadorJsonEncoder))

		except ResourceDoesNotExist as err:
			response.update({
				gcapicodes.ERROR: esmg_404 % self.get_resource_uid(*args, **kwargs) or '(none)',
				gcapicodes.STATUS: gcapicodes.FAIL		
			})
			return self.http404_resource_not_found(response=response)

		except Exception as err:
			response.update({
				gcapicodes.ERROR: emsg_500 % (self.get_resource_uid(*args, **kwargs) or '(none)', err),
				gcapicodes.STATUS: gcapicodes.FAIL,
			})
			logger.error('Unable to execute operation for resource=%s due to an error. Error="%s"\nTraceback: %s'
				% (self.get_resource_uid(*args, **kwargs) or '(none)', err, traceback.format_exc()))

			return self.send_response(json.dumps(response, cls=SonadorJsonEncoder), status_code=500)

	def get(self, output, uri, request, *args, **kwargs):
		''' Retrieve resource data
		'''		
		return self._execute_resource_request(self.orthanc_resource_json, output, uri, request, *args, 
			emsg_500='Unable to retrieve resource uid=%s. Error:\n%s', **kwargs)

	def delete(self, output, uri, request, *args, **kwargs):
		'''	Delete resource instance	
		'''
		return self._execute_resource_request(self.delete_resource, output, uri, request, *args, 
			emsg_500='Unable to delete resource uid=%s due to a server error. Error:\n%s', **kwargs)