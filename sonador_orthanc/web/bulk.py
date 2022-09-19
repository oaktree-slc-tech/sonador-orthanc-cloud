import json

from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_

from client.errors import ConfigurationError

from sonador.serialization import SonadorJsonEncoder

from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.helpers import cache_orthanc_patientjson, cache_orthanc_studyjson, cache_orthanc_seriesjson

from .base import OrthancBaseView


class CacheFetchBulkContentView(OrthancBaseView):
	'''	REST view which can be used to retrieve bulk content from the Sonador resource cache.
	'''
	sessionmaker = None

	def setup(self, output, uri, request, *args, **kwargs): 
		super().setup(output, uri, request)

		# Ensure sessionmaker instance is available
		if self.sessionmaker is None:
			raise ConfigurationError(
				'Unable to initialize %s instance: invalid session maker instance' % type(self).__name__)

		# Parse request components: resource
		request = request or {}
		self.POST = json.loads(request.get('body')) if request.get('body') else {}
		self.resources = self.POST.get('Resources', [])

	def orthanc_resourcejson(self, cresource):
		'''	Create the Orthanc JSON structure for the provided resource
		'''
		if isinstance(cresource, CacheSeries):
			return cache_orthanc_seriesjson(cresource)

		elif isinstance(cresource, CacheStudy):
			return cache_orthanc_studyjson(cresource)

		elif isinstance(cresource, CachePatient):
			return cache_orthanc_patientjson(cresource)

		raise TypeError(
			'Unable to create JSON structure for resource. "%s" is not a valid resoure type.' % type(cresource))

	def post(self, output, uri, request, *args, **kwargs):
		'''	Retrieve the list of resources requested
		'''
		with self.sessionmaker() as session:
			orthanc_resources = {}

			# Retrieve resources
			cpatient_resources = session.query(CachePatient).filter(CachePatient.uid.in_(self.resources))
			cstudy_resources = session.query(CacheStudy).options(joinedload(CacheStudy.parent))\
				.filter(CacheStudy.uid.in_(self.resources))
			cseries_resources = session.query(CacheSeries).filter(CacheSeries.uid.in_(self.resources))

			# Index resource by resource ID
			for rtype_results in (cpatient_resources, cstudy_resources, cseries_resources):
				for r in rtype_results:
					orthanc_resources[r.uid] = r

			# Sort results in the order they were requested in the request and serialize to JSON
			return self.send_response(json.dumps(
				[self.orthanc_resourcejson(orthanc_resources.get(r)) for r in self.resources if orthanc_resources.get(r)],
				cls=SonadorJsonEncoder))