import json, logging
import orthanc

from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_

import client.apisettings as gcapicodes
from client.errors import ConfigurationError
from client.utils.object import omit

from sonador.serialization import SonadorJsonEncoder
from sonador.servers.auth import SonadorUser, SonadorGroupCollection, ACL_PERM_QUERY

from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.helpers import cache_orthanc_patientjson, cache_orthanc_studyjson, cache_orthanc_seriesjson

from ..dcmquery.auth import PatientResourceAclMixin, StudyResourceAclMixin, SeriesResourceAclMixin

from .base import OrthancBaseView
from .secure_user import UserContextMixin
from .secure_search import SecureResourceQueryViewMixin

logger = logging.getLogger(__name__)


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

	def filter_patient_resources(self, session, *args, **kwargs):
		'''	Retrieve patient instances
		'''
		return session.query(CachePatient).filter(CachePatient.uid.in_(self.resources))

	def filter_study_resources(self, session, *args, **kwargs):
		'''	Retrieve study instances
		'''
		return session.query(CacheStudy).options(joinedload(CacheStudy.parent)) \
			.filter(CacheStudy.uid.in_(self.resources))

	def filter_series_resources(self, session, *args, **kwargs):
		'''	Retrieve series instances
		'''	
		return session.query(CacheSeries).filter(CacheSeries.uid.in_(self.resources))

	def post(self, output, uri, request, *args, **kwargs):
		'''	Retrieve the list of resources requested
		'''
		with self.sessionmaker() as session:
			orthanc_resources = {}

			# Retrieve resources
			cpatient_resources = self.filter_patient_resources(session, *args, **kwargs)
			cstudy_resources = self.filter_study_resources(session, *args, **kwargs)
			cseries_resources = self.filter_series_resources(session, *args, **kwargs)

			# Index resource by resource ID
			for rtype_results in (cpatient_resources, cstudy_resources, cseries_resources):
				for r in rtype_results:
					orthanc_resources[r.uid] = r

			# Sort results in the order they were requested in the request and serialize to JSON
			return self.send_response(json.dumps(
				[self.orthanc_resourcejson(orthanc_resources.get(r)) for r in self.resources if orthanc_resources.get(r)],
				cls=SonadorJsonEncoder))


class SecureResourceAclPolicyQueryHelperBase:
	'''	Base class which can be used to execute ACL policy requests. Implements a standalone interface
		that can be used to apply ACL policy constraints to cachemodel queries.
	'''
	resource_model = None

	def __init__(self, *args, **kwargs):
		'''	Initialize query helper instance
		'''
		self.resource_model = kwargs.pop('resource_model', self.resource_model)
		if not self.resource_model:
			raise ConfigurationError('Unable to initialize ACL policy query helper, invali resource model instance')


class SonadorResourceAclPolicyPatientQueryHelper(PatientResourceAclMixin, SecureResourceAclPolicyQueryHelperBase):
	'''	ACL policy query helper for cached patients
	'''
	resource_model = CachePatient


class SonadorResourceAclPolicyStudyQueryHelper(StudyResourceAclMixin, SecureResourceAclPolicyQueryHelperBase):
	'''	ACL policy query helper for cached studies
	'''
	resource_model = CacheStudy


class SonadorResourceAclPolicySeriesQueryHelper(SeriesResourceAclMixin, SecureResourceAclPolicyQueryHelperBase):
	'''	ACL policy query helper for cached series
	'''
	resource_model = CacheSeries


class SecureCacheFetchBulkContentView(UserContextMixin, CacheFetchBulkContentView):
	'''	REST view which implements an ACL mediated bulk content retrieval interface.
	'''
	sonador_manager = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)
		self.init_user_context(request, *args, **kwargs)

		# Parse bulk request
		self.rapid_lookup = self.POST.get('RapidLookup', True)

		if not self.user:
			raise ValueError('Unable to process secure resource query, invalid user instance')

		logger.warning('Bulk content request user-uid=%s username="%s" user-permissions="%s" groups="%s": %s' % (
			self.user.pk, getattr(self.user, 'username', None) or '(null)', ','.join(getattr(self.user, 'permissions', [])),
			','.join(str(g.pk) for g in self.groups) if self.groups else '(null)',
			self.POST,
		))

	def _get_user_aclgroups(self, *args, **kwargs):
		return getattr(self.user, 'groups', [])

	def filter_patient_resources(self, session, *args, **kwargs):
		'''	Retrieve patient instances
		'''
		dcm_resources = super().filter_patient_resources(session, *args, **kwargs)

		# If the user associated with the request does not have a "query" permission, 
		# filter the resources by ACL policies.
		if not ACL_PERM_QUERY in getattr(self.user, 'permissions', []):

			dcm_resources = SonadorResourceAclPolicyPatientQueryHelper().apply_acl_queryfilter(
				dcm_resources, self.user, self._get_user_aclgroups(*args, **kwargs), **kwargs)
		
		return dcm_resources

	def filter_study_resources(self, session, *args, **kwargs):
		'''	Retrieve study instances
		'''
		dcm_resources = super().filter_study_resources(session, *args, **kwargs)

		# If the user associated with the request does not have a "query" permission, 
		# filter the resources by ACL policies.
		if not ACL_PERM_QUERY in getattr(self.user, 'permissions', []):

			dcm_resources = SonadorResourceAclPolicyStudyQueryHelper().apply_acl_queryfilter(
				dcm_resources, self.user, self._get_user_aclgroups(*args, **kwargs), **kwargs)

		return dcm_resources

	def filter_series_resources(self, session, *args, **kwargs):
		'''	Retrieve series instances
		'''	
		dcm_resources = super().filter_series_resources(session, *args, **kwargs)

		# If the user associated with the request does not have a "query" permission, 
		# filter the resources by ACL policies.
		if not ACL_PERM_QUERY in getattr(self.user, 'permissions', []):

			dcm_resources = SonadorResourceAclPolicySeriesQueryHelper().apply_acl_queryfilter(
				dcm_resources, self.user, self._get_user_aclgroups(*args, **kwargs), **kwargs)

		return dcm_resources

	def post(self, output, uri, request, *args, **kwargs):
		'''	Process bulk content lookup

			1. Database lookups (RapidLookup=False): proxy to database /tools/bulk-content 
			2. Full server request/lookup (user has query permission)
			3. Scoped request: apply scoping to user request
		'''
		# Return permission denied for database lookup queries (RapidLookup = False) where
		# the user does not have the required permissions.
		if not self.rapid_lookup and not ACL_PERM_QUERY in getattr(self.user, 'permissions', []):
			self.send_response(json.dumps({
				gcapicodes.ERROR: 'Database lookup queries (RapidLooup=False) require "query" server permissions',
				gcapicodes.STATUS: gcapicodes.FAIL,
			}, cls=SonadorJsonEncoder), status_code=403)

		# Return database bulk lookup tools/bulk-content
		if not self.rapid_lookup:

			try:

				# Execute database query
				iserver = self.sonador_manager.get_internal_imageserver()
				_r = iserver._request_post(
					'/tools/bulk-content', 'Unable to execute tools/bulk-content', 
					json=omit(self.POST, ('RapidLookup',)), core_api=True)
				
				return self.send_response(_r.content)

			except orthanc.OrthancException as err:

				# Determine type of Orthanc error, set response status code and user error message
				if getattr(err, 'args', None) and len(err.args) and err.args[0] == 8:
					status_code = gcapicodes.STATUS_400
					emsg = 'Bad request'
				else:
					emsg = 'Bad request'
					status_code = gcapicodes.STATUS_400
				
				return self.send_response(json.dumps({
					gcapicodes.ERROR: emsg, gcapicodes.STATUS: gcapicodes.FAIL
				}), status_code=status_code)

		return super().post(output, uri, request, *args, **kwargs)