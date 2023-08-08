import abc

from sqlalchemy import Column, ForeignKey, Integer as SqlInteger, String as SqlString, \
	DateTime as SqlDateTime, Boolean as SqlBoolean
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type
from sqlalchemy.orm import relationship

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_REPORT
from sonador.imaging.orthanc.base import ImagingSeries, ImagingStudy, ImagingPatient

from .base import DbBase, AutoDbBase
from .cache import CacheResourceDbPropertiesMixin


class ProcedureStep(CacheResourceDbPropertiesMixin, DbBase):
	'''	Model for unified procedure step: provides a data structure able to provide status on a scheduled
		procedure, the state of its fulfillment, and what was performed.
	'''
	__tablename__ = 'sonador_worklist_procedurestep'
	__table_args__ = { 'extend_existing': True }

	# Creation timestamps
	ctime = Column(SqlDateTime())

	# FHIR request details
	patient_fhir = Column(mutable_json_type(dbtype=JSONB, nested=True))
	request_fhir = Column(mutable_json_type(dbtype=JSONB, nested=True))

	# Modality and desired priority
	modality = Column(SqlString(64))
	priority = Column(SqlString(256), nullable=True)
	
	# State timestamps: scheduled, started, complete
	scheduled = Column(SqlDateTime(), nullable=True)
	started = Column(SqlDateTime(), nullable=True)
	complete = Column(SqlDateTime(), nullable=True)

	# State and resource links
	state = Column(SqlString(64), nullable=True)
	patient_id  = Column(SqlString(64), nullable=True)
	study_id  = Column(SqlString(64), nullable=True)
	series_id = Column(SqlString(64), nullable=True)

	# Procedure details
	procedure_codes = Column(
		ARRAY(SqlString(256), as_tuple=False, dimensions=None, zero_indexes=False))