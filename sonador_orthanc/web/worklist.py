''' Web views which provide an API compatible with the Unified Procedure Step DICOM specification.
	UPS facilitates the retrieval of scheduled procedures via worklist queries and allows for
	modalities to report when a worklist item as been fulfilled.
'''
import json

from client.errors import ConfigurationError

from ..db.worklist import ProcedureStep

from .base import OrthancBaseView
from .helpers import paginate_query_results


class ProcedureStepManagementView(OrthancBaseView):
	'''	View instance which an be used to create, retrieve, and query procedure step instances from Orthanc.
	'''
	sessionmaker = None
	limit_default = 100
	offset_default = 0

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		if not self.sessionmaker:
			raise ConfigurationError('Unable to initialize procedure step management view, invalid session maker class.')

		# Set GET request and general query parameters
		request = request or {}
		self.GET = self.request.get('get', {})

		# Retrieve request components: limit and offset
		self.limit = int(self.GET.get('limit', self.limit_default))
		self.offset = int(self.GET.get('offset', self.offset_default))

	def get(self, output, uri, request):
		'''	Return a list of procedure step instances which match the request parameters
		'''
		return self.send_response(json.dumps({'hello': 'world'}))
		