from typing import ClassVar

from pydantic import constr, Field
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from client.errors import ConfigurationError
from client.utils.object import omit

from sonador.servers.auth import ACL_PERM_COMMENT_EDIT, ACL_PERM_REMOVE

from .. import apisettings as sonador_api
from .base import OrthancBaseForm, OrthancBaseModelform


class CommentValidationForm(OrthancBaseModelform):
	''' Validation model for validating the structure of resource comments
	'''
	Text: constr(strip_whitespace=True, min_length=1)
	Meta: dict = Field(None, title='Comment metadata')

	db_fieldmap: ClassVar[dict] = { 'Text': 'text', 'Meta': 'orthanc' }
	clean_omit_kwargs: ClassVar[tuple] = ('sonador_manager', 'session', 'create', 
		'update', 'resource_cachemodel', 'model', 'obj', 'parent_resource_obj', 'request_user', 'resource_perms')

	@classmethod
	def user_validation_error(cls, emsg, request_user=None):
		'''	Build the validation error raised when the request user may not perform the
			requested operation on a comment.

			@returns pydantic.ValidationError (field: User)
		'''
		err = PydanticValidationError.from_exception_data(emsg, line_errors=[
				InitErrorDetails(
					type=PydanticCustomError(sonador_api.SONAODR_OBJECT_INVALID_ERROR, emsg),
					loc=('User',), input=getattr(request_user, 'pk', None)),
			])

		# Add request user to details
		setattr(err, 'request_user', request_user)
		return err

	@classmethod
	def is_comment_author(cls, obj, request_user):
		'''	Determine whether the request user created the provided comment
		'''
		return bool(obj is not None and obj.user and request_user is not None
			and getattr(request_user, 'pk', None) == obj.user)

	@classmethod
	def validate_update_user(cls, obj, request_user, resource_perms=None):
		'''	Update rule: a comment may only be changed by the user account which created it, and
			that user must hold `comment_edit` on the parent resource. Fails closed: a comment with
			no recorded author, or a request with no identified user, cannot be updated.

			@input resource_perms (dict, default=None): the request user's permissions on the parent
				resource in the Sonador ACL vocabulary; None skips the grant check (the caller has
				established it another way).

			@raises pydantic.ValidationError
		'''
		if request_user is None:
			raise cls.user_validation_error(
				'Unable to identify the request user. Comments may only be updated by an authenticated user.')

		if not cls.is_comment_author(obj, request_user):
			raise cls.user_validation_error(
				'Request user instance does not match comment user. Comments may only be updated '
				+ 'by the user account which created them.', request_user=request_user)

		if resource_perms is not None and not resource_perms.get(ACL_PERM_COMMENT_EDIT):
			raise cls.user_validation_error(
				'Request user does not hold permission to manage comments on the resource.',
				request_user=request_user)

	@classmethod
	def validate_removal(cls, obj, request_user, resource_perms=None):
		'''	Removal rule: `remove` on the parent resource, or (`comment_edit` on the parent resource
			and the request user is the comment's author). Fails closed: no identified user, or no
			resource permissions, refuses.

			@input obj: comment model instance
			@input request_user (SonadorUser): user associated with the request
			@input resource_perms (dict): the request user's effective permissions on the parent
				resource, in the Sonador ACL vocabulary (`view`, `comment_edit`, `remove`, ...)

			@raises pydantic.ValidationError
		'''
		if request_user is None:
			raise cls.user_validation_error(
				'Unable to identify the request user. Comments may only be removed by an authenticated user.')

		resource_perms = resource_perms or {}

		if resource_perms.get(ACL_PERM_REMOVE):
			return

		if resource_perms.get(ACL_PERM_COMMENT_EDIT) and cls.is_comment_author(obj, request_user):
			return

		raise cls.user_validation_error(
			'Request user instance does not match comment user. Comments may only be removed by the '
			+ 'user account which created them, or by a user with permission to remove the resource.',
			request_user=request_user)
	
	@classmethod
	def clean(cls, *args, **kwargs):
		'''	Perform data conversion and field validation. All input and keyword arguments
			should be converted to the proper format to be used for initializing the base form instance.
			(OrthancBaseForm inherits from pydantic.BaseModel.)

			Any fields that are not in the proper form will trigger a validation error.
			Refer to https://docs.pydantic.dev/latest/api/base_model/.

			Validation rules:
			1. 	When performing updates, ensure that the request user is the same as the user
				which originally created the comment.

			@returns instance of the form class
		'''
		_update = kwargs.get('update')
		obj = kwargs.get('obj')

		# When performing updates, ensure that the request user matches the user
		# which created the original comment.
		if _update and obj:
			sonador_manager = kwargs.get('sonador_manager')
			
			# Ensure that a Sonador manager and session were provided
			if not sonador_manager:
				raise ConfigurationError('Unable to validate user ACL data, no Sonador manager provided to form')

			cls.validate_update_user(obj, kwargs.get('request_user'), resource_perms=kwargs.get('resource_perms'))

		return super().clean(*args, **omit(kwargs, cls.clean_omit_kwargs))
