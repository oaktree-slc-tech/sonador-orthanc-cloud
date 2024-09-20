import json, logging, orthanc
from urllib import parse as urlparse

import client.apisettings as gcapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist
from client.utils.object import pick, omit

from sonador.apisettings import IMAGING_SERVER_LEVEL, IMAGING_SERVER_WILDCARD, IMAGING_SERVER_LABELS, \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_IMAGE
from sonador.serialization import SonadorJsonEncoder
from sonador.servers.auth import SonadorUser, SonadorGroupCollection, ACL_PERM_QUERY

from ..dcmquery.auth import PatientResourceAclMixin, StudyResourceAclMixin, SeriesResourceAclMixin

from .base  import OrthancBaseView
from .cache import CacheBaseView
from .secure_user import UserContextMixin
from .patient import CachePatientQueryView
from .study import CacheStudyQueryView
from .series import CacheSeriesQueryView

logger = logging.getLogger(__name__)



class SecureResourceQueryViewMixin:
	'''	Mixin class which provides methods for filtering the DICOM resource list by user and group permissions.
		Should be used in connection with a secure resource query view.
	'''
	def _get_user_aclgroups(self, *args, **kwargs):
		return getattr(self.user, 'groups', [])

	def get_base_resourcelist(self, session, *args, **kwargs):
		'''	Retrieve base query for the view
		'''
		dcm_resources = super().get_base_resourcelist(session, *args, **kwargs)

		# If the user associated with the request does not have a "query" permission, 
		# filter the resources by ACL policies.
		if not ACL_PERM_QUERY in getattr(self.user, 'permissions', []):
			dcm_resources = self.apply_acl_queryfilter(
				dcm_resources, self.user, self._get_user_aclgroups(*args, **kwargs), **kwargs)

		return dcm_resources


class SecureCachePatientQueryView(
		SecureResourceQueryViewMixin, PatientResourceAclMixin, UserContextMixin, CachePatientQueryView):
	'''	Sonador view which implements a secure (ACL mediated) version of the Orthanc /cache/patients query endpoint.
		The view utilizes the Sonador "introspect" API for the imaging server to determine what resources
		a user has access to and filters results accordingly.
	'''
	sonador_manager = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self.init_user_context(request, *args, **kwargs)

		if not self.user:
			raise ValueError('Unable to process secure query, invalid user instance')


class SecureCacheStudyQueryView(
		SecureResourceQueryViewMixin, StudyResourceAclMixin, UserContextMixin,  CacheStudyQueryView):
	'''	Sonador view which implements a secure (ACL mediated) version of the Orthanc /cache/studies query endpoint.
		The view utilizes the Sonador "introspect" API for the imaging server to determine what resources
		a user has access to and filters results accordingly.
	'''
	sonador_manager = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self.init_user_context(request, *args, **kwargs)

		if not self.user:
			raise ValueError('Unable to process secure query, invalid user instance')


class SecureCacheSeriesQueryView(
		SecureResourceQueryViewMixin, SeriesResourceAclMixin, UserContextMixin, CacheSeriesQueryView):
	'''	Sonador view which implements a secure (ACL mediated) version of the Orthanc /cache/series query endpoint.
		The view utilizes the Sonador "introspect" API for the imaging server to determine what resources
		a user has access to and filters results accordingly.
	'''
	sonador_manager = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self.init_user_context(request, *args, **kwargs)

		if not self.user:
			raise ValueError('Unable to process secure query, invalid user instance')


class SecureToolsFindView(UserContextMixin, CacheBaseView):
	'''	Sonador view which implements a secure (ACL mediated) version of the Orthanc /tools/find API.
		It utilizes the Sonador "introspect" API for the imaging server to determine what resources
		a user has access to and filters results accordingly. If the user has a `query` permission
		and `rapid-lookup=false` is passed as a query-string parameter, the request will be passed
		through to the core `/tools/find` view provided by the Orthanc base API.
	'''
	# Secure resource query view classes: copies of properties such as the Sonador manager
	# are copied to the resource view during init of the tools/secure-find view.
	viewclass_patient = SecureCachePatientQueryView
	viewclass_study = SecureCacheStudyQueryView
	viewclass_series = SecureCacheSeriesQueryView
		
	# Placeholder attributes for view methods
	view_patient = None
	view_study = None
	view_series = None

	cache_dicomtags = None
	dcm_privatetags = None
	dcm_datetags = None

	@classmethod
	def as_view(cls, **initkwargs):
		'''	Initialize view methods to handle requests for patient, study, and series
			instance queries. The views are added to the secure tools/find view to allow
			for properties such as the user context to be passed without requiring
			multiple requests
		'''
		_sonador_manager = initkwargs.get('sonador_manager') or getattr(cls, 'sonador_manager', None)
		_sessionmaker = initkwargs.get('sessionmaker') or getattr(cls, 'sessionmaker', None)
		_cache_dicomtags = initkwargs.get('cache_dicomtags') or getattr(cls, 'cache_dicomtags', None)
		_dcm_privatetags = initkwargs.get('dcm_privatetags') or getattr(cls, 'dcm_privatetags', None)
		_dcm_datetags = initkwargs.get('dcm_datetags') or getattr(cls, 'dcm_datetags', None)

		# Initialize resource specific scoped/secure query views
		if initkwargs.get('view_patient') is None:
			initkwargs['view_patient'] = (initkwargs.get('viewclass_patient') or cls.viewclass_patient).as_view(
				sonador_manager=_sonador_manager, sessionmaker=_sessionmaker, cache_dicomtags=_cache_dicomtags,
				dcm_privatetags=_dcm_privatetags, dcm_datetags=_dcm_datetags)
		
		if initkwargs.get('view_study') is None:
			initkwargs['view_study'] = (initkwargs.get('viewclass_study') or cls.viewclass_study).as_view(
				sonador_manager=_sonador_manager, sessionmaker=_sessionmaker, cache_dicomtags=_cache_dicomtags,
				dcm_privatetags=_dcm_privatetags, dcm_datetags=_dcm_datetags)

		if initkwargs.get('view_series') is None:
			initkwargs['view_series'] = (initkwargs.get('viewclass_series') or cls.viewclass_series).as_view(
				sonador_manager=_sonador_manager, sessionmaker=_sessionmaker, cache_dicomtags=_cache_dicomtags,
				dcm_privatetags=_dcm_privatetags, dcm_datetags=_dcm_datetags)

		return super().as_view(**initkwargs)

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Verify that database propreties, database models, and indexing method have been provided
		'''
		if not self.cache_dicomtags:
			raise ConfigurationError('Unable to initialize secure tools/find view instance, invalid cache_dicomtags instance')
		if not self.cache_dicomtags:
			raise ConfigurationError('Unable to initialize secure tools/find view instance, invalid cache_dicomtags instance')

		super().setup(output, uri, request)
		self.init_user_context(request, *args, **kwargs)

		# Parse search request
		self.POST = json.loads(request.get('body')) if request.get('body') else {}
		self.rapid_lookup = self.POST.get('RapidLookup', True)

		if not self.user:
			raise ValueError('Unable to process secure query, invalid user instance')

		logger.warning('Search request user-uid=%s username="%s" user-permissions="%s" groups=%s: %s' % (
			self.user.pk, getattr(self.user, 'username', None) or '(null)', ','.join(getattr(self.user, 'permissions', [])),
			','.join(str(g.pk) for g in self.groups) if self.groups else '(null)',
			self.POST
		))

	def post(self, output, uri, request, *args, **kwargs):
		'''	Process the query

			1. Database lookups (RapidLookup=False or Instance level): proxy to /tools/find
			2. Full server request: return correct cache view
			3. Scoped request: return correct cache view with user scoping applied
		'''
		# Return permission denied for database lookup queries (RapidLookup=False or Instance) where
		# the user does not have the required permissions.
		if (not self.rapid_lookup or self.POST.get(IMAGING_SERVER_LEVEL) == IMAGING_SERVER_RESOURCE_IMAGE) \
			and not ACL_PERM_QUERY in getattr(self.user, 'permissions', []):

			self.send_response(json.dumps({
				gcapicodes.ERROR: 'Database lookup queries (RapidLookup=False or Instance) require "query" server permissions',
				gcapicodes.STATUS: gcapicodes.FAIL,
			}, cls=SonadorJsonEncoder), status_code=403)

		# Return database query from tools/find
		if (not self.rapid_lookup or self.POST.get(IMAGING_SERVER_LEVEL) == IMAGING_SERVER_RESOURCE_IMAGE):

			try: 
				# Execute database query
				iserver = self.sonador_manager.get_internal_imageserver()
				_r = iserver._request_post(
					'/tools/find', 'Unable to execute tools/find request', json=omit(self.POST, ('RapidLookup',)), core_api=True)
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

		# Execute scoped resource query
		if self.POST.get(IMAGING_SERVER_LEVEL) == IMAGING_SERVER_RESOURCE_PATIENT:
			return self.view_patient(output, uri, user=self.user, **request)
		elif self.POST.get(IMAGING_SERVER_LEVEL) == IMAGING_SERVER_RESOURCE_STUDY:
			return self.view_study(output, uri, user=self.user, **request)
		elif self.POST.get(IMAGING_SERVER_LEVEL) == IMAGING_SERVER_RESOURCE_SERIES:
			return self.view_series(output, uri, user=self.user, **request)

		raise ConfigurationError('Unsupported resource query level="%s"' % self.POST.get(IMAGING_SERVER_LEVEL))
