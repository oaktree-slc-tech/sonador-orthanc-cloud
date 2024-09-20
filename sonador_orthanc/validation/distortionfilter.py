from typing import ClassVar
from pydantic import constr

from sonador.apisettings import IMAGING_SERVER_DCM_TAG, IMAGING_SERVER_DCM_TAG_VALUE, \
    DCMHEADER_INSTITUTION_NAME, DCMHEADER_MANUFACTURER, DCMHEADER_MANUFACTER_MODEL_NAME, \
    DCMHEADER_SOFTWARE_VERSIONS

from .base import OrthancBaseForm, OrthancBaseModelform


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
