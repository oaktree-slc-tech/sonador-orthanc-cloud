''' API REST endpoints for the management of access to Orthanc resources. Resources in this
	module are split into three different types:

	*	Resource ACL policy API: endpoints used to create and manage user/group policies
		for a resource. Policies grant permissions to users to view, modify, and manage
		DICOM resources stored in Orthanc.
	*	Resource ACL permission lookup API: endpoints used to retrieve a complete list
		of permissions for a resource which account for both local (managed by Orthanc)
		and global (pattern-based policies managed by Sonador) policies.
	*	Orthanc Server (Admin) API: endpoints used by Sonador/Orthanc internals to 
		perform resource authorization and lookup resource permissions.
'''
import logging, posixpath, pydicom, json, copy, datetime, uuid, traceback

from pydantic import ValidationError as PydanticValidationError

import client.apisettings as gcapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist, ClientOperationError
from client.utils.object import pick, omit

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, \
	IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_IMAGE, \
	IMAGING_SERVER_AUTHREQUEST_LEVEL_LOOKUP
from sonador.serialization import SonadorJsonEncoder
from sonador.servers.auth import SonadorUser, SonadorGroupCollection, ACL_PERM_VIEW, ACL_PERM_MODIFY, ACL_PERM_REMOVE, \
	ACL_PERM_COMMENT_EDIT, ACL_PERM_COMMENT_VIEW, ACL_PERM_ACL, ACL_PERM_RESOURCE, ACL_PERM_ORTHANC_MAPPINGS

from .. import apisettings as sonador_api

from ..db.auth import UserPatientAuth, GroupPatientAuth, \
	UserStudyAuth, GroupStudyAuth, \
	UserSeriesAuth, GroupSeriesAuth
from ..db.internal import Resource, DicomIdentifiers
from ..db.helpers import orthanc_auth_resourcejson, dcmuid_fetch_dicomidentifier_model

from ..cache.web import ResourceBaseMixin

from ..web.base import OrthancBaseView
from ..web.ext import ResourceChildManagementBaseView, ResourceChildBaseRestView
from ..web.dicomweb import DicomResourceMixin, DicomUidJsonMixin
from ..web.secure_user import UserContextMixin, UserLookupMixin, GroupLookupMixin

from ..validation.base import OrthancViewValidationMixin
from ..validation.auth import AuthValidationForm, AuthExtendedValidationForm, SonadorResourceAuthorizationRequest, \
	RESOURCE_LEVEL_DB_MAPPING

logger = logging.getLogger(__name__)



# Resource ACL Policy API Endpoints

class AuthJsonMixin(GroupLookupMixin, UserLookupMixin):
	'''	Mixin which provides properties and methods to help serialize ACL data to JSON

		@property auth_policy_type_attr (str, 'group' or 'user'): toggles the type of auth policy
			managed by the view
	'''	
	def init_auth_json(self, *args, **kwargs):
		'''	Ensure that required properties to serialize ACL objects to JSON are defined on the view
		'''
		if not self.auth_policy_type_attr or not self.auth_policy_type_attr in sonador_api.AUTH_POLICY_TYPE_SUPPORTED:
			raise ConfigurationError(
				'Unable to initialize view %s, invalid authorization type=%s' % (type(self).__name__, self.auth_policy_type_attr))

	def orthanc_objectjson(self, acl):
		'''	Serialize ACL policy to JSON, add user/group details to response
		'''
		# Retrieve user/group details from lookup collection
		if getattr(self, 'acltype_collection', None) \
			and self.acltype_collection.get_modelinstance(getattr(acl, self.auth_policy_type_attr, None)):
			json_kwargs = {
				self.auth_policy_type_attr: self.acltype_collection.get_modelinstance(
					getattr(acl, self.auth_policy_type_attr, None))
			}

		# Unable to locate model instance matching the ACL type
		else:  json_kwargs = {}

		return orthanc_auth_resourcejson(acl, **json_kwargs)

	def acltype_collection_lookup(self, acl_policy_type_uids):
		'''	Retrieve user/group profiles
		'''
		_iserver = self.sonador_manager.get_internal_imageserver()
		acltype_collection = None

		# Retrieve user data
		if self.auth_policy_type_attr == sonador_api.AUTH_POLICY_TYPE_GROUP:
			acltype_collection = self.sonador_group_lookup(acl_policy_type_uids)

		# Retrieve group data
		elif self.auth_policy_type_attr == sonador_api.AUTH_POLICY_TYPE_USER:
			acltype_collection = self.sonador_user_lookup(acl_policy_type_uids)
			
		return acltype_collection

	@property
	def auth_policy_type_attr(self):
		return self.model.principal_foreignkey_attr


class AuthManagementView(AuthJsonMixin, ResourceChildManagementBaseView):
	'''	REST endpoint which can be used to create and list auth grants for a specific resource
	'''
	sessionmaker = None
	resource_cachemodel = None
	model = None
	
	modelform = AuthValidationForm

	def setup(self, output, uri, request, *args, **kwargs):
		''' Parse request options
		'''
		self.init_auth_json(*args, **kwargs)
		super().setup(output, uri, request, *args, **kwargs)

	def get_objects(self, session, *args, ruid=None, **kwargs):
		'''	Retrieve the auth grants for the specified resource
		'''
		ruid = ruid or self.get_resource_uid(*args, **kwargs)
		_acl = session.query(self.model).filter_by(resource=ruid)

		# Retrieve user or group details
		_acl_policy_type_uids = set([
			getattr(_p, self.auth_policy_type_attr) for _p in _acl if getattr(_p, self.auth_policy_type_attr, None)
		])

		if _acl_policy_type_uids:
			setattr(self, 'acltype_collection', self.acltype_collection_lookup(_acl_policy_type_uids))

		return _acl

	def init_object_model(self, ruid=None, **kwargs):
		''' Initialize new auth grant model instance
		'''
		ruid = ruid or self.get_resource_uid(*args, **kwargs)
		return self.model(uid=str(uuid.uuid4()), resource=ruid)

	def modelform_kwargs(self, **kwargs):
		'''	Add sessionm, Sonador manager, cache model, and ACL model to modelform.clean
		'''
		form_kwargs = super().modelform_kwargs(**kwargs)
		form_kwargs.update({
			**pick(kwargs, ('session', 'create')),
			**pick(self, ('sonador_manager', 'resource_cachemodel', 'model')),
			'parent_resource_obj': self.get_resource(**kwargs),
		})
		return form_kwargs


class AuthRestView(AuthJsonMixin, ResourceChildBaseRestView):
	'''	REST endpoint which can be used to retrieve details, update, and remove a specific grant auth
		associated with a resource.
	'''
	sessionmaker = None
	resource_cachemodel = None
	model = None
	modelform = AuthValidationForm

	def setup(self, output, uri, request, *args, **kwargs):
		''' Parse request options
		'''
		self.init_auth_json(*args, **kwargs)
		super().setup(output, uri, request, *args, **kwargs)

	def get_object(self, session, *args, rid=None, cid=None, **kwargs):
		'''	Retrieve auth instance specified by the resource UID and child UID. Throws ResourceDoesNotExist
			if unable to find either the parent series or an auth grant with the provided UID.

			@returns auth instance
		'''
		# Retrieve resource and grant UID
		r = kwargs.get('resource') or self.get_resource(session, ruid=rid)
		cid = cid or self.get_object_uid(*args, **kwargs)

		auth = session.query(self.model).filter_by(resource=r.publicid, uid=cid).first()
		if not auth:
			raise ResourceDoesNotExist('Unable to retrieve auth ID=%s for %s=%s' % (cid, self.resource_cachemodel.type, rid))

		if getattr(auth, self.auth_policy_type_attr, None):
			setattr(self, 'acltype_collection', self.acltype_collection_lookup([getattr(auth, self.auth_policy_type_attr)]))

		return auth

	def modelform_kwargs(self, **kwargs):
		'''	Add session, Sonador manager, cache model, and ACL model to modelform.clean
		'''
		form_kwargs = super().modelform_kwargs(**kwargs)

		# Check submitted form request and back-fill attributes missing from request
		# with those of the existing model instance
		_obj = kwargs.get('obj')
		if _obj:
			form_kwargs = self._backfill_object_attrs(_obj, attrs=form_kwargs)

		# Database session/object/update keys, sonador manager/cache model/ACL model keys, and parent resource
		form_kwargs.update({
			**pick(kwargs, ('session', 'update', 'obj')),
			**pick(self, ('sonador_manager', 'resource_cachemodel', 'model')),
			'parent_resource_obj': self.get_resource(**kwargs),
		})
		return form_kwargs


class AuthDICOMManagementView(DicomUidJsonMixin, DicomResourceMixin, AuthManagementView):
	'''	DICOMweb AuthRestView management view: list and create ACL policies
	'''
	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self._init_dicom_json(*args, **kwargs)

	def get_objects(self, session, *args, ruid=None, **kwargs):
		'''	Retrieve the instance for the view resource
		'''
		r = self.get_resource(session, *args, **kwargs)
		_acl = session.query(self.model).filter_by(resource=r.publicid)

		# Retrieve user or group details
		_acl_policy_type_uids = set([
			getattr(_p, self.auth_policy_type_attr) for _p in _acl if getattr(_p, self.auth_policy_type_attr, None)
		])

		if _acl_policy_type_uids:
			setattr(self, 'acltype_collection', self.acltype_collection_lookup(_acl_policy_type_uids))
		
		return _acl


class AuthDICOMRestView(DicomUidJsonMixin, DicomResourceMixin, AuthRestView):
	'''	DICOMweb REST view: retrieve individual Auth details, update, and delete auth grants
	'''
	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self._init_dicom_json(*args, **kwargs)



# Resource ACL Permission Lookup API Endpoints


class AuthResourcePermissionLookupView(ResourceBaseMixin, OrthancBaseView):
	''' Orthanc view used to retrieve access permissions for the requested resource:
		view, modify, remove, acl, comment_edit, comment_view.
	'''
	sonador_manager = None
	sessionmaker = None
	resource_cachemodel = None

	def setup(self, *args, **kwargs):

		if not self.sonador_manager:
			raise ConfigurationError('Unable to initialize AuthResourcePermissionLookupView, invalid Sonador manager instance')
		if not self.sessionmaker:
			raise ConfigurationError('Unable to initialize AuthResourcePermissionLookupView, invalid session maker instance')

		if not self.resource_cachemodel:
			raise ConfigurationError('Unable to initialize AuthResourcePermissionLookupView, invalid resource cache model')
		
		self.init_resource_mixin(
			resource_type=self.resource_cachemodel.type, resource_code=self.resource_cachemodel.code)

		super().setup(*args, **kwargs)

	def get_resource_permissions(self, r, *args, **kwargs):
		'''	Retrieve permissions for the resource
		'''
		_iserver = self.sonador_manager.get_internal_imageserver()
		perms = kwargs.get('perms') or { 'perms': {}}

		# Introspect resource permissions
		_creds = self.get_user_creds(self.request, *args, **kwargs)
		_r = _iserver.introspect_resource_perms(self.resource_cachemodel.type, r.publicid, _creds[0], _creds[1])

		# Retrieve user/group and resource details
		_rjson = omit(_r.json(), ('user',))
		self.user = SonadorUser(_iserver, _rjson.get('user', {}))
		self.groups = SonadorGroupCollection(_iserver, self.user._objectdata.get('groups', []))
		
		# Add resource level and UID to response
		if not perms.get('Level'):
			perms['Level'] = self.resource_cachemodel.type
		if not perms.get('ID'):
			perms['ID'] = r.publicid

		# Map permissions schema from Sonador to permissions schema for Orthanc
		for _p,_a in _rjson.get('perms', {}).items():
			if ACL_PERM_ORTHANC_MAPPINGS.get(_p):
				perms['perms'][ACL_PERM_ORTHANC_MAPPINGS.get(_p)] = _a

		return perms

	def get(self, output, uri, request, *args, **kwargs):
		''' Retrieve permissions/grants for the requested resource
		'''
		response = kwargs.get('response', {})

		try: 

			# Retrieve resource instance
			with self.sessionmaker() as session:
				r = self.get_resource(session, *args, **kwargs)

			return self.send_response(json.dumps(self.get_resource_permissions(r, *args, **kwargs)))

		# Instance does not exist
		except ResourceDoesNotExist as err:
			response.update({
				gcapicodes.ERROR: 'Resource resource=%s uid=%s does not exist' % (
					self.resource_cachemodel.type, self.get_resource_uid(*args, **kwargs) or '(none)'),
				gcapicodes.STATUS: gcapicodes.FAIL,
			})
			return self.http404_resource_not_found(response=response)

		# Internal error/exception
		except Exception as err:
			emsg = 'Server error (resource=%s uid=%s). Error: "%s".' % (
				self.resource_cachemodel.type, self.get_resource_uid(*args, **kwargs) or '(none)', err)
			response.update({ gcapicodes.ERROR: emsg, gcapicodes.STATUS: gcapicodes.FAIL, })
			logger.error('%s\nTraceback: "%s"' % (emsg, traceback.format_exc()))
			
			return self.send_response(json.dumps(response, cls=SonadorJsonEncoder), status_code=500)


class AuthDICOMResourcePermissionLookupView(DicomResourceMixin, UserContextMixin, AuthResourcePermissionLookupView):
	'''	DICOMweb Resource Permission lookup view. Retrieve access permissions for the requested resource:
		view, modify, remove, acl, comment_edit, comment_view.
	'''
	dicom_uid_header = None

	def setup(self, *args, **kwargs):
		super().setup(*args, **kwargs)

		self.dicom_uid_header = kwargs.get('dicom_uid_header', self.dicom_uid_header)
		if not self.dicom_uid_header:
			raise ConfigurationError('Unable to initialize %s, invalid DICOM UID header attribute' % type(self).__name__)

	def get_resource_permissions(self, r, *args, **kwargs):
		'''	Retrieve permissions for the resource
		'''
		# Add DICOM header UID to the response
		_rjson = super().get_resource_permissions(r, *args, **kwargs)
		_rjson[self.dicom_uid_header] = self.get_resource_uid()		

		return _rjson



# Orthanc Server (Admin) API Endpoints


class SonadorResourceAuthorizationView(OrthancViewValidationMixin, OrthancBaseView):
	'''	Sonador authorization request view. Used by the Sonador web application to query
		the Orthanc database and retrieve information about Orthanc managed (local) ACL pollicies.
	'''
	sonador_manager = None
	sessionmaker = None
	validation_form = SonadorResourceAuthorizationRequest
	success_status_code = 201
	error_status_code = 400

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Parse request options and verify that required components of the view are present
		'''
		if not self.sonador_manager:
			raise ConfigurationError(
				'Unable to initialize view %s, invalid Sonador manager instance' % type(self).__name__)

		super().setup(output, uri, request, *args, **kwargs)

		# De-serialize request data and retrieve operation parameters
		self.POST = json.loads(request.get('body')) if request.get('body') else {}

	def post(self, output, uri, request, *args, **kwargs):
		''' Determine permissions for the requested resource
		'''
		try:
			with self.sessionmaker() as session:

				# Parse validation request
				_auth_request = self.validation_form.clean(**self.POST)

				# Authorization response base: copy user, group, and resource request details from frequest
				_rjson = { 'User': _auth_request.user.ID, 'Group': _auth_request.group.ID }
				if IMAGING_SERVER_AUTHREQUEST_LEVEL_LOOKUP.get(_auth_request.level) and _auth_request.orthanc_id:
					_rjson[IMAGING_SERVER_AUTHREQUEST_LEVEL_LOOKUP[_auth_request.level]] = _auth_request.orthanc_id

				# Back-fill components of the request to ensure that the view provides the correct policies
				_auth_request = self._auth_request_transforms(session, _auth_request)

				# Add user/group ACL for requested resource
				_rjson.update(self.resource_policy(session, _auth_request))	

				return self.send_response(json.dumps(_rjson, cls=SonadorJsonEncoder))

		except PydanticValidationError as err:
			logger.error('Invalid authorization request. Error:\n%s' % err)
			return self.send_response(
				json.dumps(self.validation_error_response(err), cls=SonadorJsonEncoder),
				status_code=self.error_status_code)

		except Exception as err:
			logger.error('Unable to process authorization request due to an error. Error:\n%s\n%s' % ( err, traceback.format_exc() ))

			return self.send_response(json.dumps({
				'error': str(err), gcapicodes.STATUS: gcapicodes.FAIL
			}), status_code=400)

	def _auth_request_transforms(self, session, auth_request):
		'''	Parse, transform, and correct components to ensure that auth requests are processed correctly.

			1.	If a DICOM UID is provided but not an Orthanc UID, back-fill the Orthanc UID so that all
				relevant ACLs will be retrieved from the database.
		'''
		if getattr(auth_request, 'dicom_uid', None) and not getattr(auth_request, 'orthanc_id', None):
			
			# Retrieve DICOM resource model from the database and back-fill the Orthanc UID
			di = dcmuid_fetch_dicomidentifier_model(session, auth_request.dicom_uid)
			if di and RESOURCE_LEVEL_DB_MAPPING.get(auth_request.level) == di.resource.resourcetype:

				# Initialize new auth request form components and back-filled Orthanc UID
				auth_request = type(auth_request).clean(**{
					**auth_request.dict(), **{ 'orthanc-id': di.resource.publicid, 'dicom-uid': auth_request.dicom_uid, }
				})

				logger.warning('Missing Orthanc UID from authorization request, back-fill value from level="%s" DICOM UID="%s" resource-uid=%s' % (
					auth_request.level.value, auth_request.dicom_uid, auth_request.orthanc_id
				))

		return auth_request

	def _merge_acl(self, policy, acl_lists, permissions):
		'''	Merge the provided ACL lists into the policy. Iterates through the access policies and copies permissions
			to the unified policy if the value has not already been set to True.
		'''
		for _acl in acl_lists:
			if _acl:
				for _perm,_val in pick(_acl, permissions).items():
					if not policy.get(_perm):
						policy[_perm] = _val

		return policy

	def _fetch_patient_policy(self, session, principal, level, uid, patient_authmodel, study_authmodel, series_authmodel,
			permissions=ACL_PERM_RESOURCE, policy=None):
		'''	Parse the request to an ACL policy for the desired patient.

			*	`view` permission on a study grants limited `view` permissions on the patient.
			* 	A `modify` permission on a child study group policy in combination with a `worklist` permission on the same group grants
				a `modify` permission for the patient. This workaround is needed since the authorization plugin
				checks patient and study permissions for worklist requests and will deny the request if there is not a `modify`, 
				permission for the patient. IMPORTANT: the group on the study and the group on the worklist policy must match.
				-	TODO: Add URI to the request made by the authorization plugin so it is possible to understand which resource
					is being requested and ONLY provide the `modify` permission for worklist requests.

		'''
		policy = policy or {}
		_patient_acl = _study_acl = _series_acl = None

		# Retrieve patient auth policy
		_patient_acl = session.query(patient_authmodel).filter_by(**{
				patient_authmodel.principal_foreignkey_attr: principal, 'resource': uid
			}).first()

		# Retrieve study auth policy
		_study_acl = session.query(study_authmodel).filter_by(**{
				study_authmodel.principal_foreignkey_attr: principal,
			}).filter(study_authmodel.study.has(
				study_authmodel.resource_cachemodel.parent.has(
					patient_authmodel.resource_cachemodel.uid == uid )))\
			.first()

		# Retrieve series auth policy
		_series_acl = session.query(series_authmodel).filter_by(**{
				series_authmodel.principal_foreignkey_attr: principal
			}).filter(series_authmodel.series.has(
				series_authmodel.resource_cachemodel.parent.has(
					study_authmodel.resource_cachemodel.parent.has(
				 		patient_authmodel.resource_cachemodel.uid == uid )))) \
			.first()

		# Set view permission: start with series, then inspect study. If either permission
		# is true, then the policy should be set to view = True.
		if _series_acl:
			policy[ACL_PERM_VIEW] = getattr(_series_acl, ACL_PERM_VIEW)
		if _study_acl:
			if not policy.get(ACL_PERM_VIEW):
				policy[ACL_PERM_VIEW] = getattr(_study_acl, ACL_PERM_VIEW)

		# Set modify permission: start with study. If the study modify policy is set to true
		# and it is a group policy, retrieve the global policies from Sonador and check
		# for a worklist permission. If the group UIDs match, set the modify policy to true.
		# IMPORTANT: required in order to allow limited users to modify worklist items.
		if _study_acl and isinstance(_study_acl, GroupStudyAuth):

			# Check worklist exemption for modify policy. Fetch global server policies and inspect
			# worklist permission. If a worklist permission is available for the same group, change the
			# policy `modify` to True.
			if not policy.get(ACL_PERM_MODIFY) and _study_acl.modify:
				_global_acl = self.sonador_manager.get_internal_imageserver().fetch_acl().get_group_acl(_study_acl.group)
				
				# Worklist exception criteria: global ACL policy present which matches local study policy group,
				# the local study policy must defined a `modify` permission, AND the global policy must have a worklist permission.
				if _global_acl and _study_acl.group == _global_acl.group and getattr(_global_acl, 'worklist'):

					logger.warning(('Override modify for patient based on worklist policy: principal=%s level=%s ' 
							+' resource-uid=%s study-policy=%s study-policy-modify=%s global-policy=%s global-policy-worklist=%s acl-patient-modify=%s') % (
						principal, level, uid, _study_acl.uid, _study_acl.modify, _global_acl.pk, getattr(_global_acl, 'worklist', None), True
					))
					policy[ACL_PERM_MODIFY] = True

		# Set patient attributes from patient ACL
		if _patient_acl:

			# Set view policy
			if not policy.get(ACL_PERM_VIEW):
				policy[ACL_PERM_VIEW] = getattr(_patient_acl, ACL_PERM_VIEW)

			# Set modify policy
			if not policy.get(ACL_PERM_MODIFY):
				policy[ACL_PERM_MODIFY] = getattr(_patient_acl, ACL_PERM_MODIFY)

			# Set other attributes
			policy.update(pick(_patient_acl, (ACL_PERM_REMOVE, ACL_PERM_ACL)))

		return policy, _patient_acl, _study_acl, _series_acl

	def _fetch_resource_policy(self, session, principal, level, uid, patient_authmodel, study_authmodel, series_authmodel,
			permissions=ACL_PERM_RESOURCE):
		'''	Parse the request to an ACL policy for the desired resource. Resource grants provide both direct
			and indirect permissions. For the resource directly covered by the policy, the access permissions are taken
			directly from the policy. For indirect authorizations, the rules below are applied.

			1. Series authorizations: if `view` permission is available for a series, `view` is also granted
				for the parent study and patient. No other authorizations propagate to the parent resources.
			2. Study permissions are applied to all child series. `view` on the study grants `comments_view`
				and `modify` grants `comments_edit`. A `view` permission for the study
			3. Patient permissions are applied to all child studies and series. As in the case of a study, `view` on the patient grants
				`comments_view` and `modify` grants `comments_edit` for child series.
		'''
		policy = {}
		_patient_acl = _study_acl = _series_acl = None

		# Retrieve authorizations grants for patient queries
		if level == IMAGING_SERVER_RESOURCE_PATIENT.lower():
			policy, _patient_acl, _study_acl, _series_acl = self._fetch_patient_policy(
				session, principal, level, uid, patient_authmodel, study_authmodel, series_authmodel, permissions=permissions, policy=policy)

		# Retrieve authorization grants for study queries
		elif level == IMAGING_SERVER_RESOURCE_STUDY.lower():

			# Retrieve patient auth policy
			_patient_acl = session.query(patient_authmodel).filter_by(**{
					patient_authmodel.principal_foreignkey_attr: principal,
				}).filter(patient_authmodel.patient.has(
					patient_authmodel.resource_cachemodel.studies_collection.any(
						study_authmodel.resource_cachemodel.uid == uid ))) \
				.first()

			# Retrieve study auth policy
			_study_acl = session.query(study_authmodel).filter_by(**{
					study_authmodel.principal_foreignkey_attr: principal, 'resource': uid
				}).first()

			# Retrieve series auth policy
			_series_acl = session.query(series_authmodel).filter_by(**{
					series_authmodel.principal_foreignkey_attr: principal
				}).filter(series_authmodel.series.has(
					series_authmodel.resource_cachemodel.parent.has(
						study_authmodel.resource_cachemodel.uid == uid ))) \
				.first()

			# Set view permissions from series. Study and patient permissions are
			# layered on top. Only `view` propagates from a series authorization.
			if _series_acl:
				policy[ACL_PERM_VIEW] = getattr(_series_acl, ACL_PERM_VIEW)

			# Merge study and patient ACL permissions to the policy
			policy = self._merge_acl(policy, (_study_acl, _patient_acl), permissions=permissions)

		# Retrieve authorization grants for series queries
		elif level == IMAGING_SERVER_RESOURCE_SERIES.lower():

			# Retrieve patient auth policy
			_patient_acl = session.query(patient_authmodel).filter_by(**{
					patient_authmodel.principal_foreignkey_attr: principal,
				}).filter(patient_authmodel.patient.has(
					patient_authmodel.resource_cachemodel.studies_collection.any(
						study_authmodel.resource_cachemodel.series_collection.any(
							series_authmodel.resource_cachemodel.uid == uid )))) \
				.first()

			# Retrieve study auth policy
			_study_acl = session.query(study_authmodel).filter_by(**{
					study_authmodel.principal_foreignkey_attr: principal,
				}).filter(study_authmodel.study.has(
					study_authmodel.resource_cachemodel.series_collection.any(
						series_authmodel.resource_cachemodel.uid == uid ))) \
				.first()

			# Retrieve series auth policy
			_series_acl = session.query(series_authmodel).filter_by(**{
					series_authmodel.principal_foreignkey_attr: principal, 'resource': uid
				}).first()

			# Merge series, study, and patient ACL permissions to the policy
			policy = self._merge_acl(policy, (_series_acl, _study_acl, _patient_acl), permissions=permissions)

			# Set comment_view to True if the study or patient ACL view permission is True
			if not policy.get(ACL_PERM_COMMENT_VIEW) and (getattr(_study_acl, 'view', None) or getattr(_patient_acl, 'view', None)):
				policy[ACL_PERM_COMMENT_VIEW] = getattr(_study_acl, 'view', None) or getattr(_patient_acl, 'view', None)

			# Set comment_modify to True if study or patient ACL modify permission is True
			if not policy.get(ACL_PERM_COMMENT_EDIT) and (getattr(_study_acl, 'modify', None) or getattr(_patient_acl, 'modify', None)):
				policy[ACL_PERM_COMMENT_EDIT] = getattr(_study_acl, 'modify', None) or getattr(_patient_acl, 'modify', None)

			logger.debug('Authorization policy for series=%s level=%s orthanc-id=%s\n%s' % (principal, level, uid, policy))

		# Retrieve authorization grants for instance queries
		elif level == IMAGING_SERVER_RESOURCE_IMAGE.lower():

			# Instances do not have their own policy models, and for that reason adhere to the permissions
			# of the series to which they belong. Retrieve series UID and return the policy for the parent.
			dcm = self.sonador_manager.get_internal_imageserver().get_dcm_instance(uid)
			return self._fetch_resource_policy(session, principal, IMAGING_SERVER_RESOURCE_SERIES.lower(), dcm.series,
				patient_authmodel, study_authmodel, series_authmodel, permissions=permissions)

		# Filter null values from the policy
		return pick(policy, permissions)

	def resource_policy(self, session, auth_request, permissions=ACL_PERM_RESOURCE):
		'''	Parse the request to an ACL policy for the desired resource from user and group authorizations.
			(Defers to _fetch_resource_policy to determine permissions from the authorization model classes.)
			Access to the resource may be covered by a user grant or a group policy, if there are instances of both,
			the the policies will be reconciled and access granted if a positive permission is found.

			@returns dict of permissions
		'''
		policy = {}

		# Retrieve user and group authorizations relevant to the resource request
		logger.debug('Sonador resource authorization request: user="%s" level="%s" orthanc-id="%s" dicom-uid="%s" resource="%s" method="%s"' % (
			auth_request.user.username, auth_request.level.value, auth_request.orthanc_id, auth_request.dicom_uid, auth_request.uri, auth_request.method.value
		))

		_user_authpolicy = self._fetch_resource_policy(session,
			auth_request.user.ID, auth_request.level.value, auth_request.orthanc_id, UserPatientAuth, UserStudyAuth, UserSeriesAuth,
			permissions=permissions)
		_group_authpolicy = self._fetch_resource_policy(session,
			auth_request.group.ID, auth_request.level.value, auth_request.orthanc_id, GroupPatientAuth, GroupStudyAuth, GroupSeriesAuth,
			permissions=permissions)

		# Merge user and group policy
		for _perm in permissions:
			policy[_perm] = _user_authpolicy.get(_perm) or _group_authpolicy.get(_perm)

		return pick(policy, permissions)
