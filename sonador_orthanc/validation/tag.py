import datetime

from typing import ClassVar
from pydantic import constr
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from sonador.apisettings import IMAGING_SERVER_DCM_TAG, IMAGING_SERVER_DCM_TAG_VALUE, \
    DCMHEADER_INSTITUTION_NAME, DCMHEADER_MANUFACTURER, DCMHEADER_MANUFACTER_MODEL_NAME, \
    DCMHEADER_SOFTWARE_VERSIONS

from .base import OrthancBaseForm, OrthancBaseModelform
from .. import apisettings as sonador_api

from client.utils.object import gextend, omit
from client.errors import ConfigurationError

from .base import OrthancBaseModelform, SonadorGroupValidationMixin, SonadorPolicyValidationMixin

class TagValidationForm(SonadorPolicyValidationMixin, SonadorGroupValidationMixin, OrthancBaseModelform):
    ''' Form for validating the structure of device list entries
    '''
    Value: constr(strip_whitespace=True, min_length=1)
    Meaning: constr(strip_whitespace=True, min_length=1)
    SchemeDesignator: constr(strip_whitespace=True, min_length=1)
    SchemeVersion: constr(strip_whitespace=True) = None

    db_fieldmap: ClassVar[dict] = {    
        'Value': 'value',
        'Meaning': 'meaning',
        'SchemeDesignator': 'scheme_designator',
        'SchemeVersion': 'scheme_version',
    }
    
    clean_omit_kwargs: ClassVar[tuple] = ('sonador_manager', 'session', 'create', 'update', 'model', 'obj', 'group',
        'request_user', 'request_user_groups')

    policy_feature: ClassVar[str] = 'tag'
    policy_feature_label: ClassVar[str] = 'Series tags'
    # Sonador has no staff rule for Series Tags: staff follow the member rule
    policy_staff_manages: ClassVar[bool] = False

    @classmethod
    def clean(cls, *args, **kwargs):
        '''	Perform data conversion and field validation. All input and keyword arguments
            should be converted to the proper format to be used for initializing the base form instance.
            (OrthancBaseForm inherits from pydantic.BaseModel.)

            The group's policy must enable Series Tags and the request user must be allowed to
            manage them (see SonadorPolicyValidationMixin) before the fields are validated.

            Any fields that are not in the proper form will trigger a validation error.
            Refer to https://docs.pydantic.dev/latest/api/base_model/.            
        '''
        sonador_manager = kwargs.get('sonador_manager')
        group = kwargs.get('group')
        if not sonador_manager:
            raise ConfigurationError('Unable to validate tag, no Sonador manager provided to form')
        if group is None:
            raise ConfigurationError('Unable to validate tag, no group provided')

        cls.validate_policy_write(sonador_manager, group, kwargs.get('request_user'), kwargs.get('request_user_groups'))

        return super().clean(*args, **omit(kwargs, cls.clean_omit_kwargs))
