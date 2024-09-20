from typing import ClassVar

from pydantic import constr, Field
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from client.utils.object import omit

from .. import apisettings as sonador_api
from .base import OrthancBaseForm, OrthancBaseModelform


class CommentValidationForm(OrthancBaseModelform):
	''' Validation model for validating the structure of resource comments
	'''
	Text: constr(strip_whitespace=True, min_length=1)
	Meta: dict = Field(None, title='Comment metadata')

	db_fieldmap: ClassVar[dict] = { 'Text': 'text', 'Meta': 'orthanc' }
	clean_omit_kwargs: ClassVar[tuple] = ('sonador_manager', 'session', 'create', 
		'update', 'resource_cachemodel', 'model', 'obj', 'parent_resource_obj', 'request_user')
	
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

			request_user = kwargs.get('request_user')

			if obj.user and request_user and request_user.pk != obj.user:
				emsg = 'Request user instance does not match comment user. Comments may only be updated ' \
					+ 'by the user account which created them.'
				err = PydanticValidationError.from_exception_data(emsg, line_errors=[
						InitErrorDetails(
							type=PydanticCustomError(sonador_api.SONAODR_OBJECT_INVALID_ERROR, emsg),
							loc=('User',), msg='Request user does not match comment user'),
					])

				# Add request user to details and raise error
				setattr(err, 'request_user', request_user)
				raise err

		return super().clean(*args, **omit(kwargs, cls.clean_omit_kwargs))