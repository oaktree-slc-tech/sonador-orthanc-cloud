import abc, logging, datetime
from typing import Union, Sequence
from collections import OrderedDict

from sqlalchemy import Column, ForeignKey, Integer as SqlInteger, String as SqlString, \
	DateTime as SqlDateTime, Boolean as SqlBoolean
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type
from sqlalchemy.orm import relationship

from client.utils.object import pick
from client.utils.decorators import classproperty

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_IMAGE, IMAGING_SERVER_RESOURCE_REPORT, \
	DicomDatetimePairKey, DicomDatetimePair
from sonador.imaging.orthanc.base import ImagingSeries, ImagingStudy, ImagingPatient, DcmInstance

from .base import DbBase, AutoDbBase

logger = logging.getLogger(__name__)


class CacheResourceDbPropertiesMixin:
	'''	Mixin class providing Orthanc identifiers and common fields.
	'''
	uid = Column(SqlString(64), primary_key=True, unique=True)
	orthanc = Column(mutable_json_type(dbtype=JSONB, nested=True))
	mtime = Column(SqlDateTime(), nullable=True)
	stable = Column(SqlBoolean(), nullable=True)


class CacheResourceMixin(CacheResourceDbPropertiesMixin):
	'''	Mixin class providing Orthanc identifiers and cache fields for an Orthanc
		MainDicomTags response.
	'''
	__table_args__ = { 'extend_existing': True }

	@classmethod
	def _init_cache_instance(cls, session, rinstance, mtime=None):
		'''	Initialize (or retrieve) the cached version of the resource

			@input session: SQLAlchemy database session
			@input rinstance: Sonador resource instance from which the cache instance should
				be initialized

			@returns cache resource model instance
		'''
		ci = session.query(cls).filter_by(uid=rinstance.pk).first()

		# Initialize new instance, set the modified time to the instance lastupdate timestamp
		if not ci:
			ci = cls(uid=rinstance.pk)
			mtime = rinstance.lastupdate

		# Update existing instance, set modified time to the current server's time
		else: mtime = datetime.datetime.now()

		# Copy Orthanc DICOM properties to cache instance
		ci.orthanc = rinstance.dicomdata
		ci.mtime = mtime
		return ci

	@classmethod
	def _init_privatetags(cls, privatetags_resource_model, session, rinstance, dcm_privatetags, **kwargs):
		'''	Initialize (or retrieve) the cached version of the resource's private tags

			@input privatetags_resource_model: SQLAlchemy resource model used to store the
				the private tags for the instance.
			@input session: SQLAlchemy database session
			@input rinstance: Sonador resource instance for which the private tags model
				should be retrieved or initialized.

			@returns instance of privatetags_resource_model
		'''
		pci = session.query(privatetags_resource_model).filter_by(uid=rinstance.pk).first()

		# Initialize new instance, set the modified time to the instance lastupdate timestamp
		if not pci:
			pci = privatetags_resource_model(uid=rinstance.pk)
			mtime = rinstance.lastupdate

		# Update existing instance, set the modified time to the current server's time
		else: mtime = datetime.datetime.now()

		# Copy Orthanc DICOM private tags to cache instance
		dtags = cls._get_dcmtags(rinstance)

		pci.orthanc = pick(dtags, dcm_privatetags)
		pci.stable = rinstance.stable
		pci.mtime = mtime
		logger.debug('Private Tags: resource=%s\n%s' % (pci.uid, pci.orthanc))
		return pci

	@classmethod
	def _init_dcmdatetag(cls, datetime_resource_model, session, rinstance, dcm_datetag_val, **kwargs):
		'''	Initialize (or retrieve) the cached model for the provided instance and date/time headers.

			@input datetime_resource_model: SQLAlchemy resource model used to store the date/time
				tags for the instance.
			@input session: SQLAlchemy database session.
			@input rinstance: Sonador resource instance for which the date/time model should
				be retrieved or initialized.

			@returns instance of datetime_resource_model
		'''
		dci = session.query(datetime_resource_model).filter_by(
			uid=rinstance.pk, date_tag=dcm_datetag_val.meta.date_tag, time_tag=dcm_datetag_val.meta.time_tag).first()
		if not dci:
			dci = datetime_resource_model(
				uid=rinstance.pk, date_tag=dcm_datetag_val.meta.date_tag, time_tag=dcm_datetag_val.meta.time_tag)

		dci.ts = dcm_datetag_val.ts
		logger.debug('Date/Time Tag: resource=%s date-tag=%s time-tag=%s value=%s'
			% (rinstance.pk, dcm_datetag_val.meta.date_tag, dcm_datetag_val.meta.time_tag, dcm_datetag_val.ts))
		return dci

	@classproperty
	@abc.abstractmethod
	def privatetags_resource_model(cls):
		'''	Private DICOM tags extension model associated with the resource
		'''

	@classproperty
	@abc.abstractmethod
	def datetime_resource_model(cls):
		'''	Date/time extension model associated with the resource
		'''

	@classmethod
	@abc.abstractmethod
	def _get_dcmtags(cls, rinstance):
		'''	Retrieve the private tags for the provided resource instance

			@input rinstance: Sonador resource instance for which the private tags should be retrieved
		'''

	@classmethod
	def _get_dcmdatetags(cls,
			instance: Union[ImagingPatient, ImagingStudy, ImagingSeries, DcmInstance],
			dcm_datetags: Sequence[DicomDatetimePairKey], dcache=None,**kwargs):
		''' Retrieve the provided date/time tags from the instance.

			@input instance (ImagingPatient, ImagingStudy, or ImagingSeries instance): instance from
				which the data should be taken.
			@input dcm_datetimes (iterable of DicomDatetimePairKey instances): header values
				to retrieve from the instance.

			@returns OrderedDict: date time dags and the associated values. The dictionary
				 returns DicomDatetimePair objects keyed to the provided DicomDatetimePairKey instances.
		'''
		dcache = dcache or OrderedDict()

		# Retrieve date/time tags
		for dmeta in dcm_datetags:

			# Omit pairs for which the date tag is not defined
			if instance.dicomdata.get(dmeta.date_tag):
				dcache[dmeta] = DicomDatetimePair(
					instance.dicomdata.get(dmeta.date_tag), instance.dicomdata.get(dmeta.time_tag), meta=dmeta)

		return dcache

	@classmethod
	@abc.abstractmethod
	def index(self, session, instance, *args, link=True, commit=True, **kwargs):
		''' Initialize a copy of the resource in the index.
		'''


class CacheAclResourceMixin:
	'''	Mixin class which defines properties and methods for working with user and group 
		access control (ACL) models.
	'''
	@classproperty
	@abc.abstractmethod
	def group_acl_model(cls):
		'''	Database model associated with group ACL policies
		'''

	@classproperty
	@abc.abstractmethod
	def user_acl_model(cls):
		'''	Database model associated with user ACL policies
		'''


class CachePatient(CacheAclResourceMixin, CacheResourceMixin, DbBase):
	__tablename__ = 'sonador_cache_patient'

	birth_date = Column(SqlDateTime(), nullable=True)

	studies = Column(ARRAY(SqlString(64), as_tuple=False, dimensions=None, zero_indexes=False))
	studies_collection = relationship(
		'CacheStudy', back_populates='parent', overlaps='studies_collection,parent', viewonly=True)

	privatetags = relationship('CachePatientPrivateTags', overlaps='patient,privatetags', back_populates='patient',
		primaryjoin='CachePatient.uid == foreign(CachePatientPrivateTags.uid)', uselist=False,
		cascade='all,delete-orphan')
	timestamp_tags = relationship('CachePatientDatetime', overlaps='patient,timestamp_tags', back_populates='patient',
		primaryjoin='CachePatient.uid == foreign(CachePatientDatetime.uid)', cascade='all,delete-orphan')

	auth_user = relationship('UserPatientAuth', overlaps='patient,auth_user', back_populates='patient',
		primaryjoin='CachePatient.uid == foreign(UserPatientAuth.resource)', cascade='all,delete-orphan')
	auth_group = relationship('GroupPatientAuth', overlaps='patient,auth_group', back_populates='patient',
		primaryjoin='CachePatient.uid == foreign(GroupPatientAuth.resource)', cascade='all,delete-orphan')

	@classproperty
	def type(cls):
		return IMAGING_SERVER_RESOURCE_PATIENT

	@classproperty
	def code(cls):
		from .internal import ORTHANCDB_PATIENT_TYPE
		return ORTHANCDB_PATIENT_TYPE

	@classproperty
	def privatetags_resource_model(cls):
		from .dcmext import CachePatientPrivateTags
		return CachePatientPrivateTags

	@classproperty
	def datetime_resource_model(cls):
		'''	Date/time extension model associated with the resource
		'''
		from .dcmext import CachePatientDatetime
		return CachePatientDatetime

	@classproperty
	def group_acl_model(self):
		from .auth import GroupPatientAuth
		return GroupPatientAuth

	@classproperty
	def user_acl_model(cls):
		from .auth import UserPatientAuth
		return UserPatientAuth

	@classmethod
	def _get_dcmtags(cls, instance, study_idx=0, series_idx=0, dcm_idx=0):
		'''	Retrieve DICOM tags from specified instance for patient

			@input study_idx (int, default=0): index number to use to retrieve study
			@input series_idx (int, default=0): series index number from which the DICOM instance
				will be taken
			@Input dcm_idx (int, default=0): index to use for retrieving DICOM instance
		'''
		if not len(instance.studies_collection):
			raise ValueError('Unable to retrieve DICOM tags, patient=%s has no child studies.' % instance.pk)

		s = instance.studies_collection[study_idx]
		if not len(s.series_collection):
			raise ValueError('Unable to retrieve DICOM tags, patient=%s study=%s instance has no child series.'
				% (instance.pk, s.pk))

		sx = s.series_collection[series_idx]
		if not len(sx.slices_collection):
			raise ValueError('Unable to retrieve DICOM tags, patient=%s study=%s series=%s has no child instances.'
				% (instance.pk, s.pk, sx.pk))

		dcm0 = sx.slices_collection[0]
		return dcm0.tags

	@classmethod
	def index(cls, session, instance: ImagingPatient, commit=True, dcm_privatetags=None, dcm_datetags=None, **kwargs):
		'''	Initialize a copy of the patient in the index
		'''
		ci = cls._init_cache_instance(session, instance)
		ci.studies = instance.studies
		ci.birth_date = instance.birth_date

		# Cache private tags
		if dcm_privatetags:
			pci = cls._init_privatetags(
				cls.privatetags_resource_model, session, instance, dcm_privatetags)
			session.add(pci)

		# Created indexed copies of date/time tags
		if dcm_datetags:
			for dcm_datetag_val in cls._get_dcmdatetags(instance, dcm_datetags=dcm_datetags).values():
				dci = cls._init_dcmdatetag(
					cls.datetime_resource_model, session, instance, dcm_datetag_val)
				session.add(dci)

		# Add cached instance to session and (if indicated) commit
		session.add(ci)
		if commit:
			session.commit()

		return ci


class CacheStudy(CacheAclResourceMixin, CacheResourceMixin, DbBase):
	__tablename__ = 'sonador_cache_study'

	ts = Column(SqlDateTime(), nullable=True)
	modalities = Column(ARRAY(SqlString, as_tuple=False, dimensions=None, zero_indexes=False))
	series = Column(ARRAY(SqlString(64), as_tuple=False, dimensions=None, zero_indexes=False))

	parent_id = Column(
		SqlString(64), ForeignKey('sonador_cache_patient.uid', ondelete='CASCADE'), nullable=True)
	parent = relationship('CachePatient', overlaps='parent,studies_collection', viewonly=True)

	series_collection = relationship(
		'CacheSeries', back_populates='parent', overlaps='parent,series_collection', viewonly=True)

	privatetags = relationship('CacheStudyPrivateTags', overlaps='study,privatetags', back_populates='study',
		primaryjoin='CacheStudy.uid == foreign(CacheStudyPrivateTags.uid)',uselist=False, cascade='all,delete-orphan')
	timestamp_tags = relationship('CacheStudyDatetime', overlaps='study,timestamp_tags', back_populates='study',
		primaryjoin='CacheStudy.uid == foreign(CacheStudyDatetime.uid)', cascade='all,delete-orphan')
	
	worklist_reviewer = relationship('StudyReviewerWorklistItem', overlaps='study,worklist_reviewer', back_populates='study',
		primaryjoin='CacheStudy.uid == foreign(StudyReviewerWorklistItem.resource)',cascade='all,delete-orphan')

	comments = relationship('ImagingStudyComment', overlaps='study,comments', back_populates='study',
		primaryjoin='CacheStudy.uid == foreign(ImagingStudyComment.study_id)', cascade='all,delete-orphan')

	worklist_reviewer = relationship('StudyReviewerWorklistItem', overlaps='study,worklist_reviewer', back_populates='study',
		primaryjoin='CacheStudy.uid == foreign(StudyReviewerWorklistItem.resource)', cascade='all,delete-orphan')

	auth_user = relationship('UserStudyAuth', overlaps='study,auth_user', back_populates='study',
		primaryjoin='CacheStudy.uid == foreign(UserStudyAuth.resource)', cascade='all,delete-orphan')
	auth_group = relationship('GroupStudyAuth', overlaps='study,auth_group', back_populates='study',
		primaryjoin='CacheStudy.uid == foreign(GroupStudyAuth.resource)', cascade='all,delete-orphan')

	@classproperty
	def type(cls):
		return IMAGING_SERVER_RESOURCE_STUDY

	@classproperty
	def code(cls):
		from .internal import ORTHANCDB_STUDY_TYPE
		return ORTHANCDB_STUDY_TYPE

	@classproperty
	def privatetags_resource_model(cls):
		from .dcmext import CacheStudyPrivateTags
		return CacheStudyPrivateTags

	@classproperty
	def datetime_resource_model(cls):
		'''	Date/time extension model associated with the resource
		'''
		from .dcmext import CacheStudyDatetime
		return CacheStudyDatetime

	@classproperty
	def group_acl_model(self):
		from .auth import GroupStudyAuth
		return GroupStudyAuth

	@classproperty
	def user_acl_model(cls):
		from .auth import UserStudyAuth
		return UserStudyAuth

	@classproperty
	def worklist_reviewer_model(cls):
		from .worklist import StudyReviewerWorklistItem
		return StudyReviewerWorklistItem

	@classproperty
	def parent_resource_model(cls):
		return CachePatient

	@classmethod
	def _get_dcmtags(cls, instance, series_idx=0, dcm_idx=0):
		'''	Retrieve DICOM tags from specified instance for study

			@input series_idx (int, default=0): series index number to use to retrieve DICOM instance
			@input dcm_idx (int, default=0): index to use for retrieving DICOM instance
		'''
		if not len(instance.series_collection):
			raise ValueError('Unable to retrieve DICOM tags, study=%s has no child series.' % instance.pk)

		sx = instance.series_collection[series_idx]
		if not len(sx.slices_collection):
			raise ValueError('Unable to retrieve DICOM tags, study=%s series=%s has no child instances.'
				% (instance.pk, sx.pk))

		dcm0 = sx.slices_collection[dcm_idx]
		return dcm0.tags

	@classmethod
	def index(cls, session, instance: ImagingStudy,
			link=True, commit=True, dcm_privatetags=None, dcm_datetags=None, **kwargs):
		'''	Initialize a copy of the study in the index
		'''
		ci = cls._init_cache_instance(session, instance)
		ci.series = getattr(instance, 'series', [])
		ci.modalities = list(set([sx.modality for sx in instance.series_collection if sx.modality]))
		ci.ts = instance.ts

		# Add database references
		if link:
			ci.parent_id = instance.patient

		# Cache private tags
		if dcm_privatetags:
			pci = cls._init_privatetags(
				cls.privatetags_resource_model, session, instance, dcm_privatetags)
			session.add(pci)

		# Created indexed copies of date/time tags
		if dcm_datetags:
			for dcm_datetag_val in cls._get_dcmdatetags(instance, dcm_datetags=dcm_datetags).values():
				dci = cls._init_dcmdatetag(
					cls.datetime_resource_model, session, instance, dcm_datetag_val)
				session.add(dci)

		# Add cached instance to session and (if indicated) commit
		session.add(ci)
		if commit:
			session.commit()

		return ci


class CacheSeries(CacheAclResourceMixin, CacheResourceMixin, DbBase):
	__tablename__ = 'sonador_cache_series'

	ts = Column(SqlDateTime(), nullable=True)
	instances = Column(ARRAY(SqlString(64), as_tuple=False, dimensions=None, zero_indexes=False))

	parent_id = Column(
		SqlString(64), ForeignKey('sonador_cache_study.uid', ondelete='CASCADE'), nullable=True)
	parent = relationship('CacheStudy', overlaps='series_collection,parent', viewonly=True)

	instances_collection = relationship(
		'CacheInstance', back_populates='parent', overlaps='parent,instances_collection', viewonly=True)

	privatetags = relationship('CacheSeriesPrivateTags', overlaps='series,privatetags', back_populates='series',
		primaryjoin='CacheSeries.uid == foreign(CacheSeriesPrivateTags.uid)', uselist=False, cascade='all,delete-orphan')
	timestamp_tags = relationship('CacheSeriesDatetime', overlaps='series,timestamp_tags', back_populates='series',
		primaryjoin='CacheSeries.uid == foreign(CacheSeriesDatetime.uid)', cascade='all,delete-orphan')
	
	comments = relationship('ImagingSeriesComment', overlaps='series,comments', back_populates='series',
		primaryjoin='CacheSeries.uid == foreign(ImagingSeriesComment.series_id)', cascade='all,delete-orphan')
	
	auth_user = relationship('UserSeriesAuth', overlaps='series,auth_user', back_populates='series',
		primaryjoin='CacheSeries.uid == foreign(UserSeriesAuth.resource)', cascade='all,delete-orphan')
	auth_group = relationship('GroupSeriesAuth', overlaps='series,auth_group', back_populates='series',
		primaryjoin='CacheSeries.uid == foreign(GroupSeriesAuth.resource)', cascade='all,delete-orphan')

	@classproperty
	def type(cls):
		return IMAGING_SERVER_RESOURCE_SERIES

	@classproperty
	def code(cls):
		from .internal import ORTHANCDB_SERIES_TYPE
		return ORTHANCDB_SERIES_TYPE

	@classproperty
	def privatetags_resource_model(cls):
		from .dcmext import CacheSeriesPrivateTags
		return CacheSeriesPrivateTags

	@classproperty
	def datetime_resource_model(cls):
		'''	Date/time extension model associated with the resource
		'''
		from .dcmext import CacheSeriesDatetime
		return CacheSeriesDatetime

	@classproperty
	def comment_model(cls):
		from .comments import ImagingSeriesComment
		return ImagingSeriesComment

	@classproperty
	def group_acl_model(self):
		from .auth import GroupSeriesAuth
		return GroupSeriesAuth

	@classproperty
	def user_acl_model(cls):
		from .auth import UserSeriesAuth
		return UserSeriesAuth

	@classproperty
	def parent_resource_model(cls):
		return CacheStudy

	@classmethod
	def _get_dcmtags(cls, instance, dcm_idx=0):
		'''	Retrieve DICOM tags from specified instance for series

			@input dcm_idx (int, default=0): index to use for retrieving DICOM instance
		'''
		if not len(instance.slices_collection):
			raise ValueError('Unable to retrieve DICOM tags, series=%s has no child instances.' % instance.pk)

		dcm0 = instance.slices_collection[dcm_idx]
		return dcm0.tags

	@classmethod
	def index(cls, session, instance: ImagingSeries,
			link=True, commit=True, dcm_privatetags=None, dcm_datetags=None, **kwargs):
		'''	Initialize a copy of the series in the index
		'''
		ci = cls._init_cache_instance(session, instance)
		ci.instances = instance.slices
		ci.ts = instance.ts

		# Add database references
		if link:

			# Link to parent study
			ci.parent_id = instance.study

			# Iterate through slices/instances and link to current series as parent
			if ci.instances:

				for dcm_uid in ci.instances:
					dcm = session.query(CacheInstance).filter_by(uid=dcm_uid).first()
					if dcm:
						dcm.parent_id = ci.uid
						session.add(dcm)

		# Cache private tags
		if dcm_privatetags:
			pci = cls._init_privatetags(
				cls.privatetags_resource_model, session, instance, dcm_privatetags)
			session.add(pci)

		# Created indexed copies of date/time tags
		if dcm_datetags:
			for dcm_datetag_val in cls._get_dcmdatetags(instance, dcm_datetags=dcm_datetags).values():
				dci = cls._init_dcmdatetag(
					cls.datetime_resource_model, session, instance, dcm_datetag_val)
				session.add(dci)

		# Add cached instance to session and (if indicated) commit
		session.add(ci)
		if commit:
			session.commit()

		return ci


class CacheInstance(CacheResourceMixin, DbBase):
	__tablename__ = 'sonador_cache_instance'

	ts = Column(SqlDateTime(), nullable=True)
	series_idx = Column(SqlInteger)

	file_uid = Column(SqlString(64))
	file_size = Column(SqlInteger)

	parent_id = Column(
		SqlString(64), ForeignKey('sonador_cache_series.uid', ondelete='CASCADE'), nullable=True)
	parent = relationship('CacheSeries', overlaps='instances_collection,parent', viewonly=True)

	privatetags = relationship('CacheInstancePrivateTags', overlaps='instance,privatetags', back_populates='instance',
		primaryjoin='CacheInstance.uid == foreign(CacheInstancePrivateTags.uid)', uselist=False, cascade='all,delete-orphan')
	timestamp_tags = relationship('CacheInstanceDatetime', overlaps='instance,timestamp_tags', back_populates='instance',
		primaryjoin='CacheInstance.uid == foreign(CacheInstanceDatetime.uid)', cascade='all,delete-orphan')

	@classproperty
	def type(cls):
		return IMAGING_SERVER_RESOURCE_IMAGE

	@classproperty
	def code(cls):
		from .internal import ORTHANCDB_INSTANCE_TYPE
		return ORTHANCDB_INSTANCE_TYPE

	@classproperty
	def privatetags_resource_model(cls):
		from .dcmext import CacheInstancePrivateTags
		return CacheInstancePrivateTags

	@classproperty
	def datetime_resource_model(cls):
		from .dcmext import CacheInstanceDatetime
		return CacheInstanceDatetime

	@classproperty
	def parent_resource_model(cls):
		return CacheSeries

	@classmethod
	def _get_dcmtags(cls, instance):
		'''	Retrieve DICOM tags from specified instance for study
		'''
		return instance.tags

	@classmethod
	def index(cls, session, instance: DcmInstance,
			link=True, commit=True, dcm_privatetags=None, dcm_datetags=None, **kwargs):
		'''	Initialize a copy of the instance in the index
		'''
		ci = cls._init_cache_instance(session, instance)
		ci.file_size = instance.file_size
		ci.file_uid = instance.file_uid
		ci.ts = instance.ts

		# Add database references
		if link:
			ci.parent_id = instance.series

		# Cache private tags
		if dcm_privatetags:
			cpi = cls._init_privatetags(
				cls.privatetags_resource_model, session, instance, dcm_privatetags)
			session.add(cpi)

		# Created indexed copies of date/time tags
		if dcm_datetags:
			for dcm_datetag_val in cls._get_dcmdatetags(instance, dcm_datetags=dcm_datetags).values():
				dci = cls._init_dcmdatetag(
					cls.datetime_resource_model, session, instance, dcm_datetag_val)
				session.add(dci)

		# Add cached instance to session and (if indicated) commit
		session.add(ci)
		if commit:
			session.commit()

		return ci



SONADOR_CACHE_MODELS = {
	IMAGING_SERVER_RESOURCE_PATIENT: CachePatient,
	IMAGING_SERVER_RESOURCE_STUDY: CacheStudy,
	IMAGING_SERVER_RESOURCE_SERIES: CacheSeries,
	IMAGING_SERVER_RESOURCE_IMAGE: CacheInstance,
}
