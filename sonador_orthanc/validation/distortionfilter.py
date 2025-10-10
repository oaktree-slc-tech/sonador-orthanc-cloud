from typing import ClassVar
from pydantic import constr

from pydantic import ValidationError as PydanticValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from client.utils.object import gextend, omit
from client.utils.conversion import str2bool
from client.errors import ClientOperationError, ConfigurationError

from sonador.apisettings import IMAGING_SERVER_DCM_TAG, IMAGING_SERVER_DCM_TAG_VALUE, \
    DCMHEADER_INSTITUTION_NAME, DCMHEADER_MANUFACTURER, DCMHEADER_MANUFACTER_MODEL_NAME, \
    DCMHEADER_SOFTWARE_VERSIONS

from .base import OrthancBaseForm, OrthancBaseModelform, SonadorUserValidationMixin, SonadorGroupValidationMixin


class DistortionFilterDeviceValidationForm(OrthancBaseModelform):
    ''' Form for validating the structure of device list entries
    '''
    InstitutionName: constr(strip_whitespace=True, min_length=1)
    Manufacturer: constr(strip_whitespace=True, min_length=1)
    ManufacturerModelName: constr(strip_whitespace=True, min_length=1)
    SoftwareVersions: constr(strip_whitespace=True, min_length=1)
    DcmTag: constr(strip_whitespace=True, min_length=1)
    DcmTagValue: constr(strip_whitespace=True, min_length=1)

    db_fieldmap: ClassVar[dict] = {
        DCMHEADER_INSTITUTION_NAME: 'institution_name',
        DCMHEADER_MANUFACTURER: 'manufacturer',
        DCMHEADER_MANUFACTER_MODEL_NAME: 'manufacturer_modelname',
        DCMHEADER_SOFTWARE_VERSIONS: 'software_versions',
        IMAGING_SERVER_DCM_TAG: 'dcm_tag_name',
        IMAGING_SERVER_DCM_TAG_VALUE: 'dcm_tag_value'
    }

    clean_omit_kwargs: ClassVar[tuple] = (
        'sonador_manager', 'session', 'create', 'update', 'model', 'obj', 'parent_resource_obj', 'group',
        'request_user', 'request_user_groups')

    @classmethod
    def clean(cls, *args, **kwargs):
        ''' Perform data conversion and field validation. All input and keyword arguments
            shouldb e converted to the proper format to be used for initializing the base form instance.
            (OrthancBaseForm inherits from pydantic.BaseModel.)

            Any fields that are not in the proper form will trigger a validation error.
            Refer to https://docs.pydantic.dev/latest/api/base_model/.

            Validation rules:

            1. Ensure that the specified group exists and is associted with the server.
            2. Ensure that the specified user exists and has access to the user.
            3. Ensure that the user has device list management permissions to create,
               modify, or change attributes of a device list entry.
        '''
        _create = kwargs.get('create')
        _update = kwargs.get('update')
        
        # Sonador manager, resourcs, and database components needed for validation
        sonador_manager = kwargs.get('sonador_manager')
        model = kwargs.get('model')
        session = kwargs.get('session')
        obj = kwargs.get('obj')
        _user = kwargs.get('request_user')

        # Ensure that a Sonador manager and session were provided
        if not sonador_manager:
            raise ConfigurationError('Unable to validate device list item, no Sonador manager provided to form')
        if not session:
            raise ConfigurationError('Unable to validate device list item data data no database session provided to form')
        if not model:
            raise ConfigurationError('Unable to validate device list item data, no model class provided')
        if not _user:
            raise ConfigurationError('Unable to validate device list item data, no user instance provided.')

        # Check to see if the specified group exists
        _group = kwargs.get('group')
        group = _group.pk

        # Ensure that the group has worklists enabled
        _acl = sonador_manager.get_internal_imageserver().fetch_acl().get_group_acl(_group.pk)
        if not getattr(_acl, 'worklist', False):
            emsg = 'Invalid group device list policy. Group %s must include an active device list policy to %s device list instances' % (
                _group.pk, 'create' if _create else 'update' if _update else '(undefined)')
            err = PydanticValidationError.from_exception_data(emsg, line_errors=[
                InitErrorDetails(
                    type=PydanticCustomError(sonador_api.SONAODR_OBJECT_INVALID_ERROR, emsg),
                    loc=('Group',), input={ 'Group': group },
                    msg=emsg)
            ])
            raise err

        # Ensure that the request user is a member of the specified group
        if not any(group == g.get('id') for g in _user.groups):
            emsg = 'Request user="%s" not a member of group="%s". User must be a member of the group in order to create a device list item.' % (_user.username, _group.name)
            err = PydanticValidationError.from_exception_data(emsg, line_errors=[
                InitErrorDetails(
                    type=PydanticCustomError(sonador_api.SONAODR_OBJECT_INVALID_ERROR, emsg),
                    loc=('Group',), input={ 'Group': group },
                    msg=emsg)
            ])

        _data = super().clean(*args, **omit(kwargs, cls.clean_omit_kwargs))
        return _data