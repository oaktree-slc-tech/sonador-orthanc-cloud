import logging
from operator import itemgetter
from enum import Enum

from typing import Optional, List, ClassVar
from pydantic import constr, Field
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from client.utils.object import gextend, omit
from client.errors import ClientOperationError
from client.errors import ConfigurationError

from sonador.apisettings import IMAGING_SERVER_RESOURCE_IMAGE
from .. import apisettings as sonador_api
from ..db.internal import ORTHANCDB_PATIENT_TYPE, ORTHANCDB_STUDY_TYPE, \
	ORTHANCDB_SERIES_TYPE, ORTHANCDB_INSTANCE_TYPE

from .base import OrthancBaseForm, OrthancBaseModelform, \
	SonadorUserValidationMixin, SonadorGroupValidationMixin

logger = logging.getLogger(__name__)


class AuthValidationForm(OrthancBaseModelform):
	''' Validation model for validating the structure of resource grants
	'''	
	View: bool
	Modify: bool
	Remove: bool
	ACL: bool

	db_fieldmap: ClassVar[dict] = {	
		'View': 'view',
		'Modify': 'modify',
		'Remove': 'remove',
		'ACL': 'acl',
	}
	clean_omit_kwargs: ClassVar[tuple] = (
		'sonador_manager', 'session', 'create', 'update', 'resource_cachemodel', 'model', 'obj', 'parent_resource_obj')


class AuthExtendedValidationForm(AuthValidationForm):
	'''	Validation model for validating the structure of resource grants which include comments
	'''
	CommentEdit: bool
	CommentView: bool

	db_fieldmap: ClassVar[dict] = gextend(AuthValidationForm.db_fieldmap, {
		'CommentEdit': 'comment_edit',
		'CommentView': 'comment_view',
	})



# Validate User Access Control Policies


class UserAclValidationMixin(SonadorUserValidationMixin):
	'''	Mixin class for validation forms which provides methods to verify user properties
	'''
	@classmethod
	def clean(cls, *args, **kwargs):
		''' Perform data conversion and field validation. All input arguments and keyword arguments
			should be converted to the proper format to be used for initializing the base form
			instance (OrthancBaseForm inherits from pydantic.BaseModel).

			Any fields that are not in the proper form will trigger a validation error.
			Refer to https://docs.pydantic.dev/latest/api/base_model/.

			Validation rules:
			1.	Check for existing pollicy
			2.	Ensure that the specified user exists and has access to the server

			@returns instance of the form class
		'''
		_create = kwargs.get('create')
		_update = kwargs.get('update')
		sonador_manager = kwargs.get('sonador_manager')
		resource_cachemodel = kwargs.get('resource_cachemodel')
		model = kwargs.get('model')
		session = kwargs.get('session')
		parent_obj = kwargs.get('parent_resource_obj')
		obj = kwargs.get('obj')

		# Ensure that a Sonador manager and session were provided
		if not sonador_manager:
			raise ConfigurationError('Unable to validate user ACL data, no Sonador manager provided to form')
		if not session:
			raise ConfigurationError('Unable to validate user ACL data no database session provided to form')
		if not resource_cachemodel:
			raise ConfigurationError('Unable to validate user ACL data, no cache model class provided')
		if not model:
			raise ConfigurationError('Unable to validate user ACL data, no ACl model class provided')

		# Check to see if a policy matching the current user and resource exists
		user = kwargs.get('User')
		if _create and user and parent_obj:
			_acl = session.query(model).filter_by(resource=parent_obj.publicid, user=user).first()

			# Duplicate policy found
			if _acl:

				# ACL error
				emsg = 'ACL policy for type=%s resource=%s user=%s already exists' % (
					resource_cachemodel.type, parent_obj.publicid, user
				)
				err = PydanticValidationError.from_exception_data(emsg, line_errors=[
						InitErrorDetails(
							type=PydanticCustomError(sonador_api.SONAODR_OBJECT_DUPLICATE_ERROR, emsg),
							loc=('User',), input={ 'User': user, }, 
							msg='ACL policy for user already exists'),
					])

				# Add ACL details and raise error
				setattr(err, 'obj_data', { 'ID': _acl.uid })
				raise err

		# Check to see if the specified user exists and has access to the server
		cls.validate_user(sonador_manager, user, fieldname='User')

		return super().clean(*args, **omit(kwargs, cls.clean_omit_kwargs))


class UserAclValidationForm(UserAclValidationMixin, AuthValidationForm):
	'''	Validation model for ACL policies associated with users
	'''
	User: int

	db_fieldmap: ClassVar[dict] = gextend(AuthValidationForm.db_fieldmap, {
		'User': 'user',
	})


class UserAclExtendedValidationForm(UserAclValidationMixin, AuthExtendedValidationForm):
	'''	Validation model for ACL policies associated with users that include comments
	'''
	User: int

	db_fieldmap: ClassVar[dict] = gextend(AuthExtendedValidationForm.db_fieldmap, {
		'User': 'user',
	})



# Validate Group Access Control Policies


class GroupAclValidationMixin(SonadorGroupValidationMixin):
	'''	Mixin class for validation forms which provides methods to verify that a group exists
		and is associated with the imaging server.
	'''
	@classmethod
	def clean(cls, *args, **kwargs):
		'''	Perform data conversion and field validation. All input arguments and keyword arguments
			should be converted to the proper format to be used for initializing the base form instance.
			(OrthancBaseForm inherits from pydanyic.BaseModel.)

			Any fields that are not in the proper form will trigger a validation error.
			Refer to https://docs.pydantic.dev/latest/api/base_model/.

			Validation rules:
			1. Check for existing policy for the resource and group
			2. Ensure that the specified group exists and is associated with the server

			@returns instance of the form class
		'''
		_create = kwargs.get('create')
		_update = kwargs.get('update')
		sonador_manager = kwargs.get('sonador_manager')
		resource_cachemodel = kwargs.get('resource_cachemodel')
		model = kwargs.get('model')
		session = kwargs.get('session')
		parent_obj = kwargs.get('parent_resource_obj')
		obj = kwargs.get('obj')

		# Ensure that a Sonador manager and session were provided
		if not sonador_manager:
			raise ConfigurationError('Unable to validate group ACL data, no Sonador manager provided to form')
		if not session:
			raise ConfigurationError('Unable to validate group ACL data no database session provided to form')
		if not resource_cachemodel:
			raise ConfigurationError('Unable to validate group ACL data, no cache model class provided')
		if not model:
			raise ConfigurationError('Unable to validate group ACL data, no ACl model class provided')

		# Check to see if a policy matching the current group and resource exists
		group = kwargs.get('Group')
		if _create and group and parent_obj:
			_acl = session.query(model).filter_by(resource=parent_obj.publicid, group=group).first()

			# Duplicate policy found
			if _acl:

				# ACL errror
				emsg = 'ACL policy for type=%s resource=%s group=%s already exists' % (
					resource_cachemodel.type, parent_obj.publicid, group
				)
				err = PydanticValidationError.from_exception_data(emsg, line_errors=[
					InitErrorDetails(
						type=PydanticCustomError(sonador_api.SONAODR_OBJECT_DUPLICATE_ERROR, emsg),
						loc=('Group',), input={ 'Group': group, },
						msg='ACL policy for group already exists'),
				])

				# Add ACl details adn raise error
				setattr(err, 'obj_data', { 'ID': _acl.uid })
				raise err

		# Check to see if the specified group exists
		cls.validate_group(sonador_manager, group, fieldname='Group')

		return super().clean(*args, **omit(kwargs, cls.clean_omit_kwargs))


class GroupAclValidationForm(GroupAclValidationMixin, AuthValidationForm):
	'''	Validation model for ACL policies associated with groups
	'''
	Group: int

	db_fieldmap: ClassVar[dict] = gextend(AuthValidationForm.db_fieldmap, {
		'Group': 'group',
	})


class GroupAclExtendedValidationForm(GroupAclValidationMixin, AuthExtendedValidationForm):
	'''	Validation model for ACL policies associated with users that include comments
	'''
	Group: int

	db_fieldmap: ClassVar[dict] = gextend(AuthExtendedValidationForm.db_fieldmap, {
		'Group': 'group',
	})



# Sonador Resource Authorization Requests


class ResourceLevels(str, Enum):
	'''	Types of resources
	'''
	PATIENT = sonador_api.IMAGING_SERVER_RESOURCE_PATIENT.lower()
	STUDY = sonador_api.IMAGING_SERVER_RESOURCE_STUDY.lower()
	SERIES = sonador_api.IMAGING_SERVER_RESOURCE_SERIES.lower()
	INSTANCE = IMAGING_SERVER_RESOURCE_IMAGE.lower()
	SYSTEM = 'system'


RESOURCE_LEVEL_DB_MAPPING = {
	sonador_api.IMAGING_SERVER_RESOURCE_PATIENT.lower(): ORTHANCDB_PATIENT_TYPE,
	sonador_api.IMAGING_SERVER_RESOURCE_STUDY.lower(): ORTHANCDB_STUDY_TYPE,
	sonador_api.IMAGING_SERVER_RESOURCE_SERIES.lower(): ORTHANCDB_SERIES_TYPE,
	IMAGING_SERVER_RESOURCE_IMAGE.lower(): ORTHANCDB_INSTANCE_TYPE,
}


class ResourceRequestMethods(str, Enum):
	'''	Types of resource requests
	'''
	GET = 'get'
	POST = 'post'
	PUT = 'put'
	DELETE = 'delete'


class SonadorGroup(OrthancBaseForm):
	'''	Sonador group
	'''
	ID: int 
	name: str


class SonadorUser(OrthancBaseForm):
	'''	Sonador user
	'''
	ID: int 
	username: str
	email: str
	groups: Optional[List[SonadorGroup]]


class SonadorResourceAuthorizationRequest(OrthancBaseForm):
	'''	Validation form for verifying the structure of Sonador autbhorization requests.
		Authorization requests are made from Sonador -> Orthanc to check the 
		local permissions of resources.
	'''
	# Resource UIDs
	orthanc_id: Optional[str] = Field(alias='orthanc-id', default=None)
	dicom_uid: Optional[str] = Field(alias='dicom-uid', default=None)
	
	# User and group identifiers
	user: SonadorUser
	group: SonadorGroup
	
	# Request type and methods	
	level: Optional[ResourceLevels]
	method: ResourceRequestMethods
	uri: Optional[str] = None
