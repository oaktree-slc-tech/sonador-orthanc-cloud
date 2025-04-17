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

from .base import OrthancBaseModelform, SonadorUserValidationMixin

class UserPreferencesValidationForm(SonadorUserValidationMixin, OrthancBaseModelform):
    ''' Form for validating the structure of user preferences
    '''
    Data: constr(strip_whitespace=True, min_length=1)

    db_fieldmap: ClassVar[dict] = {    
        'Data': 'data',
    }