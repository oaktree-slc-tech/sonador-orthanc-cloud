import abc

from sqlalchemy import Column, ForeignKey, Integer as SqlInteger, BigInteger as SqlBigInteger, String as SqlString, \
	DateTime as SqlDateTime, Boolean as SqlBoolean, event, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type
from sqlalchemy.orm import relationship

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_REPORT
from sonador.imaging.orthanc.base import ImagingSeries, ImagingStudy, ImagingPatient

from client.utils.decorators import classproperty

from .base import DbBase, AutoDbBase
from .cache import CacheResourceDbPropertiesMixin
from .helpers import set_ctime, set_mtime


class ProcedureStep(CacheResourceDbPropertiesMixin, DbBase):
	'''	Model for unified procedure step: provides a data structure able to provide status on a scheduled
		procedure, the state of its fulfillment, and what was performed.

		Refer to https://dicom.nema.org/medical/dicom/current/output/html/part18.html#chapter_11.
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


class ReviewerWorklistMixin:
	''' Reviewer Worklist Mixin
	'''
	uid = Column(SqlString(64), primary_key=True, unique=True)
	group = Column(SqlBigInteger)
	user = Column(SqlBigInteger)

	# Resource, state, and completion timestamps
	resource = Column(SqlString(64))
	state = Column(SqlString(512))
	orthanc = Column(mutable_json_type(dbtype=JSONB, nested=True))


class StudyReviewerWorklistItem(ReviewerWorklistMixin, DbBase):
	''' Model for encapsulated worklist items based on group authority
	'''
	__tablename__ = 'sonador_worklist_reviewer_study_workitem'
	__table_args__ = { 'extend_existing': True }

	study = relationship('CacheStudy', back_populates='worklist_reviewer',
		primaryjoin='foreign(StudyReviewerWorklistItem.resource) == CacheStudy.uid',
		viewonly=True, uselist=False)

	# Creation, modification, and completion times
	ctime = Column(SqlDateTime())
	mtime = Column(SqlDateTime())
	complete = Column(SqlDateTime(), nullable=True)

	@classproperty
	def principal_foreignkey_attr(cls):
		'''	Foreign key column that maps to the principal (group) associated with the policy
		'''
		return 'group'

	@classproperty
	def type(self):
		return 'Study Reviewer Worklist Item'



# Database events


# Procedure Step
event.listens_for(ProcedureStep, 'before_insert')(set_ctime)


# Study reviewer work item
event.listens_for(StudyReviewerWorklistItem, 'before_insert')(set_ctime)
event.listens_for(StudyReviewerWorklistItem, 'before_update')(set_mtime)