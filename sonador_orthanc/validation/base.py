import abc, copy, logging

from typing import Optional, List, ClassVar
from pydantic import BaseModel as BaseValidationModel, constr, Field
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

import client.apisettings as gapi
from client.utils.object import gextend, omit, pick
from client.errors import ClientOperationError
from client.errors import ConfigurationError

from .. import apisettings as sonador_api

logger = logging.getLogger(__name__)


class OrthancViewValidationMixin:
	'''	Mixin class which provides properties for working with validation forms
	'''
	def err2msg(self, err, msg_fields=('field', gapi.CODE, gapi.MSG, 'input')):
		'''	Convert the provided 
		'''
		if err.get('loc'):
			# loc entries may be ints (list indices, e.g. Sequence item validation); stringify
			err['field'] = '.'.join(str(_l) for _l in err['loc'])
		if err.get('type'):
			err[gapi.CODE] = err['type']
		if err.get('msg'):
			err[gapi.MSG] = err['msg']

		return pick(err, msg_fields)

	def validation_error_response(self, err):
		''' Parse the provided validation error to the Sonador API format
			and return a JSON structure to be returned to the user.

			@input err (pydantic.ValidationError): error instance to be parsed

			@returns dict
		'''		
		server_errors = {}

		# Convert error to field list and message
		for _e in err.errors():
			_field = '.'.join(str(_l) for _l in _e.get('loc', tuple()))
			_msg = self.err2msg(_e)

			if server_errors.get(_field):
				server_errors[_field].append(_msg)
			else: server_errors[_field] = [_msg]

		_json = {
			gapi.STATUS: gapi.FAIL, gapi.ERRORS: server_errors,
			gapi.MSG: '%s' % err.title
		}

		# Check error for object data, add to response
		if hasattr(err, 'obj_data'):
			_json[gapi.OBJECT_DATA] = err.obj_data

		return _json


class OrthancBaseForm(BaseValidationModel, abc.ABC):
	'''	Base class for Orthanc data validation forms. Builds on top of pydantic.BaseModel
		with pattern inspirations from Django forms. The entrypoint to a form instance is
		intended to be the "clean" method, which should be used for performing conversion
		and cleaning operations.
	'''
	@classmethod
	def clean(cls, *args, **kwargs):
		''' Perform data conversion and field validation. All input arguments and keyword arguments
			should be converted to the proper format to be used for initializing the base form
			instance (OrthancBaseForm inherits from pydantic.BaseModel).

			Any fields that are not in the proper form will trigger a validation error.
			Refer to https://docs.pydantic.dev/latest/api/base_model/.

			@returns instance of the form class
		'''
		return cls(*args, **kwargs)


class OrthancBaseModelform(OrthancBaseForm):
	'''	Base class for form instances intended to be used with SQLAlchemy model instances within Orthanc.
		Provides methods to enable the persistence of model attributes to the database.
	'''
	def save(self, session, dbmodel, *args, commit=True, **kwargs):
		''' Persist data from the form to the provided model model instance

			@returns dbmodel
		'''
		# Iterate through fields defined by the form and set the associated value
		# on the database instance.
		for fname, fvalue in self.dict().items():
			setattr(dbmodel, self.db_fieldmap[fname] if fname in self.db_fieldmap else fname, fvalue)

		# Commit model to session
		if commit:
			session.add(dbmodel)
			session.commit()

		return dbmodel


class SonadorUserValidationMixin:
	'''	Mixin class which provides methods for validating Sonador users
	'''
	@classmethod
	def validate_user(cls, sonador_manager, user: int, fieldname='User'):
		'''	Check that the provided user ID exists and the account has access to the server.

			@raises PydanticValidationError
		'''
		# Check to see if the specified user exists and has access to the server
		try:
			_iserver = sonador_manager.get_internal_imageserver()
			_users = _iserver.user_lookup([user])
			return _users

		except ClientOperationError as err:

			# Unable to retrieve details for user
			emsg = ('Unable to retrieve details for user=%s. '
				+ 'User does not exist or does not have access to the server.') % user
			err = PydanticValidationError.from_exception_data(emsg, line_errors=[
					InitErrorDetails(
						type=PydanticCustomError(sonador_api.SONAODR_OBJECT_INVALID_ERROR, emsg),
						loc=(fieldname,), input={ fieldname: user },
						msg='User does not exist or does not have access to the server.'),
				])

			raise err


class SonadorGroupValidationMixin:
	'''	Mixin class which provides methods for validating Sonador groups
	'''
	@classmethod
	def validate_group(cls, sonador_manager, group: int, fieldname='Group'):
		'''	Check that the provided group ID exists and is associated with the server.

			@raises PydanticValidationError
		'''
		# Check to see if the specified group exists
		try:
			_iserver = sonador_manager.get_internal_imageserver()
			_groups = _iserver.group_lookup([group])
			return _groups

		except ClientOperationError as err:

			# Unable to retrieve details for group
			emsg = 'Unable to retrieve details for group=%s. Group instance not associated with server.' % group
			err = PydanticValidationError.from_exception_data(emsg, line_errors=[
				InitErrorDetails(
					type=PydanticCustomError(sonador_api.SONAODR_OBJECT_INVALID_ERROR, emsg),
					loc=(fieldname,), input={ fieldname: group },
					msg='Group not associated with server.'),
			])

			raise err
	

def policy_feature_permissions(acl, user, feature, staff_manages=False):
	'''	Effective permissions of a user on one group policy for a feature the group curates.
		`read` is the feature being enabled on the policy, or the user being a superuser (Sonador
		authorizes every read for superusers). `manage` requires the feature to be enabled and the
		modify flag, or superuser status, or staff status where Sonador's authorization grants
		staff the feature (`staff_manages`; true for Display Attributes, not for Series Tags): the
		same rule validate_policy_write enforces and the same one Sonador's introspection applies,
		so what is advertised is what a write will be allowed. `acl` may be None for a group
		without a policy.

		@returns dict: `{ <feature>: read, <feature>_modify: manage }`
	'''
	enabled = bool(getattr(acl, feature, False))
	superuser = bool(getattr(user, 'is_superuser', False))
	staff = staff_manages and bool(getattr(user, 'is_staff', False))
	manage = enabled and (bool(getattr(acl, '%s_modify' % feature, False)) or superuser or staff)

	return { feature: enabled or superuser, '%s_modify' % feature: manage }


class SonadorPolicyValidationMixin:
	'''	Write rule for objects a group curates under one of its server policy features (Series
		Tags, Display Attributes). The feature must be enabled on the group's policy for this
		server; a regular user must also be a member of the group holding the feature's modify
		flag, while superusers, and staff where Sonador grants them the feature, only need the
		feature enabled. Refusals follow the comments forms: a validation error on the User
		field, answered as a 400 by the views.

		@property policy_feature (str): policy flag that enables the feature, e.g. `display_attr`
		@property policy_feature_label (str): how the feature is named in error messages
		@property policy_staff_manages (bool): whether staff manage every enabled group without
			membership or the modify flag. Mirrors Sonador's authorization, which has that staff
			rule for Display Attributes only.
	'''
	policy_feature: ClassVar[str] = None
	policy_feature_label: ClassVar[str] = None
	policy_staff_manages: ClassVar[bool] = False

	@classmethod
	def policy_modify_flag(cls):
		return '%s_modify' % cls.policy_feature

	@classmethod
	def policy_permissions(cls, acl, user):
		'''	The user's effective permissions on one group policy, as the collection headers and the
			aggregate report them and as the write rule enforces them. Membership is the caller's
			concern (Sonador authorizes the request; the aggregate iterates the user's own groups).

			@returns dict: `{ <feature>: read, <feature>_modify: manage }`
		'''
		return policy_feature_permissions(acl, user, cls.policy_feature, staff_manages=cls.policy_staff_manages)

	@classmethod
	def policy_validation_error(cls, emsg, request_user=None):
		err = PydanticValidationError.from_exception_data(emsg, line_errors=[
			InitErrorDetails(
				type=PydanticCustomError(sonador_api.SONAODR_OBJECT_INVALID_ERROR, emsg),
				loc=('User',), input=getattr(request_user, 'pk', None)),
		])
		setattr(err, 'request_user', request_user)
		return err

	@classmethod
	def request_user_group_ids(cls, request_user, request_user_groups=None):
		'''	Group ids the request user belongs to, from the user profile and the group collection
		'''
		ids = set()
		for membership in (getattr(request_user, '_objectdata', None) or {}).get('groups') or []:
			if isinstance(membership, dict) and membership.get('id') is not None:
				ids.add(int(membership['id']))
		for g in (request_user_groups or []):
			pk = getattr(g, 'pk', None)
			if pk is not None:
				ids.add(int(pk))
		return ids

	@classmethod
	def validate_policy_write(cls, sonador_manager, group, request_user, request_user_groups=None):
		'''	Refuse a create, update or removal unless the group's policy enables the feature and
			the request user may manage it.

			@returns the group's policy
			@raises pydantic.ValidationError
		'''
		if not cls.policy_feature:
			raise ConfigurationError('Unable to validate policy, form does not name a policy feature')
		if request_user is None:
			raise cls.policy_validation_error(
				'Unable to identify the request user. %s may only be changed by an authenticated user.' % cls.policy_feature_label)

		acl = sonador_manager.get_internal_imageserver().fetch_acl().get_group_acl(int(group.pk))
		if not getattr(acl, cls.policy_feature, False):
			raise cls.policy_validation_error(
				'%s are not enabled for group "%s" on this server.' % (cls.policy_feature_label, group.name), request_user=request_user)

		if getattr(request_user, 'is_superuser', False) or (cls.policy_staff_manages and getattr(request_user, 'is_staff', False)):
			return acl

		if int(group.pk) not in cls.request_user_group_ids(request_user, request_user_groups) \
				or not cls.policy_permissions(acl, request_user)[cls.policy_modify_flag()]:
			raise cls.policy_validation_error(
				'User "%s" does not hold permission to manage the %s of group "%s".' % (
					getattr(request_user, 'username', None) or getattr(request_user, 'pk', None),
					cls.policy_feature_label.lower(), group.name), request_user=request_user)

		return acl
