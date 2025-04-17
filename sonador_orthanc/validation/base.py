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
			err['field'] = '.'.join(err['loc'])
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
			_field = '.'.join(_e.get('loc', tuple()))
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
	