import logging, datetime

from typing import Optional, List, ClassVar, Literal
from pydantic import constr, Field
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from client.utils.object import gextend, omit
from client.utils.conversion import str2bool
from client.errors import ClientOperationError
from client.errors import ConfigurationError

from sonador.apisettings.worklists import SONADOR_WORKLIST_STATUS_APPROVED, SONADOR_WORKLIST_STATUS_REJECTED, \
	SONADOR_WORKLIST_STATUS_UNREAD, SONADOR_WORKLIST_STATUS_REVIEWED

from .. import apisettings as sonador_api

from .base import OrthancBaseForm, OrthancBaseModelform, SonadorUserValidationMixin, SonadorGroupValidationMixin

logger = logging.getLogger(__name__)


class WorklistItemValidationForm(SonadorGroupValidationMixin, SonadorUserValidationMixin, OrthancBaseModelform):
	''' Validation model for validating the structure of worklist
	'''	
	State: Literal[SONADOR_WORKLIST_STATUS_REJECTED, SONADOR_WORKLIST_STATUS_APPROVED, 
		SONADOR_WORKLIST_STATUS_UNREAD, SONADOR_WORKLIST_STATUS_REVIEWED]
	Group: int
	User: int
	Complete: datetime.datetime = Field(None, title='Complete', description='Mark worklist as complete')
	Meta: dict = Field(None, title='Worklist metadata')

	db_fieldmap: ClassVar[dict] = {	
		'State': 'state',
		'Group': 'group',
		'User': 'user',
		'Meta': 'orthanc',
		'Complete': 'complete'
	}
	clean_omit_kwargs: ClassVar[tuple] = (
		'sonador_manager', 'session', 'create', 'update', 'resource_cachemodel', 'model', 'obj', 'parent_resource_obj')

	@classmethod
	def clean(cls, *args, **kwargs):
		'''	Perform data conversion and field validation. All input and keyword arguments
			should be converted to the proper format to be used for initializing the base form instance.
			(OrthancBaseForm inherits from pydantic.BaseModel.)

			Any fields that are not in the proper form will trigger a validation error.
			Refer to https://docs.pydantic.dev/latest/api/base_model/.

			Validation rules:
			1. Ensure that the specified group exists and is associated with the server.
			2. Ensure that the specified user exists and has access to the server.
			3. Prevent a group from being changed after the worklist item has been created.
			4. If an item has a complete timestamp, prevent modification.
		'''
		_create = kwargs.get('create')
		_update = kwargs.get('update')

		# Sonador manager, resourcs, and database components needed for validation
		sonador_manager = kwargs.get('sonador_manager')
		resource_cachemodel = kwargs.get('resource_cachemodel')
		model = kwargs.get('model')
		session = kwargs.get('session')
		parent_obj = kwargs.get('parent_resource_obj')
		obj = kwargs.get('obj')

		# Ensure that a Sonador manager and session were provided
		if not sonador_manager:
			raise ConfigurationError('Unable to validate worklist data, no Sonador manager provided to form')
		if not session:
			raise ConfigurationError('Unable to validate worklist data data no database session provided to form')
		if not resource_cachemodel:
			raise ConfigurationError('Unable to validate worklist data data, no cache model class provided')
		if not model:
			raise ConfigurationError('Unable to validate worklist data, no worklist model class provided')

		# Check to see if the specified group exists
		group = kwargs.get('Group')
		cls.validate_group(sonador_manager, group, fieldname='Group')

		# Check to see if the specified user exists
		user = kwargs.get('User')
		cls.validate_user(sonador_manager, user, fieldname='User')

		# Set the complete field to current datetime if 'Complete' key is present. If complete is not
		# present or a negative value
		if kwargs.get('Complete') or str2bool(kwargs.get('Complete')):
			kwargs['Complete'] = datetime.datetime.now()
		else: kwargs['Complete'] = None
 
		# Ensure that group provided in the update request matches the value in the database.
		if _update and group != obj.group:
			emsg = 'Invalid group. It is not possible to change the value of Group for an existing worklist item.'
			err = PydanticValidationError.from_exception_data(emsg, line_errors=[
				InitErrorDetails(
					type=PydanticCustomError(sonador_api.SONAODR_OBJECT_INVALID_ERROR, emsg),
					loc=('Group',), input={ 'Group': group },
					msg=emsg),
			])
			raise err 

		# If an item has a complete timestamp, prevent modification.
		if _update and obj.complete:
			emsg = 'Worklist item marked as complete. Worklist items cannot be modified once they are set as complete.'
			err = PydanticValidationError.from_exception_data(emsg, line_errors=[
				InitErrorDetails(
					type=PydanticCustomError(sonador_api.SONAODR_OBJECT_INVALID_ERROR, emsg),
					loc=('Complete',), msg=emsg),
			])
			raise err 

		return super().clean(*args, **omit(kwargs, cls.clean_omit_kwargs))