import abc, logging

from sqlalchemy import Column, ForeignKey, Integer as SqlInteger, String as SqlString, \
	DateTime as SqlDateTime, Boolean as SqlBoolean
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type
from sqlalchemy.orm import relationship

from client.utils.object import pick
from client.utils.decorators import classproperty

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_REPORT
from sonador.imaging.orthanc.base import ImagingSeries, ImagingStudy, ImagingPatient

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
	def _init_cache_instance(cls, session, rinstance):
		'''	Initialize (or retrieve) the cached version of the resource

			@input session: SQLAlchemy database session
			@input rinstance: Sonador resource instance from which the cache instance should
				be initialized

			@returns cache resource model instance
		'''
		ci = session.query(cls).filter_by(uid=rinstance.pk).first()
		if not ci:
			ci = cls(uid=rinstance.pk)

		# Copy Orthanc DICOM properties to cache instance
		ci.orthanc = rinstance.dicomdata
		ci.mtime = rinstance.lastupdate
		return ci

	@classmethod
	def _init_privatetags(cls, privatetags_resource_model, session, rinstance, dcm_privatetags, **kwargs):
		'''	Initialize (or retrieve) the cached version of the resource's private tags

			@input privatetags_resource_model: SQLAlchemy resource model used to store the
				the private tags for the instance.
			@input session: SQLAlchemy dataabase session
			@input rinstance: Sonador resource instance for which the private tags model
				should be retrieved or initialized.

			@returns instance privatetags_resource_model
		'''
		pci = session.query(privatetags_resource_model).filter_by(uid=rinstance.pk).first()
		if not pci:
			pci = privatetags_resource_model(uid=rinstance.pk)

		# Copy Orthanc DICOM private tags to cache instance
		dtags = cls._get_dcmtags(rinstance)

		pci.orthanc = pick(dtags, dcm_privatetags)
		pci.stable = rinstance.stable
		pci.mtime = rinstance.lastupdate
		logger.debug('Private Tags: resource=%s\n%s' % (pci.uid, pci.orthanc))
		return pci

	@classproperty
	@abc.abstractmethod
	def privatetags_resource_model(self):
		'''	Private DICOM tags extension model associated with the resource
		'''

	@classmethod
	@abc.abstractmethod
	def _get_dcmtags(cls, rinstance):
		'''	Retrieve the private tags for the provided resource instance

			@input rinstance: Sonador resource instance for which the private tags should be retrieved
		'''

	@classmethod
	@abc.abstractmethod
	def index(self, session, instance, *args, link=True, commit=True, **kwargs):
		''' Initialize a copy of the resource in the index.
		'''


class CachePatient(CacheResourceMixin, DbBase):
	__tablename__ = 'sonador_cache_patient'

	birth_date = Column(SqlDateTime(), nullable=True)

	privatetags = relationship('CachePatientPrivateTags', overlaps='patient,privatetags', back_populates='patient',
		primaryjoin='CachePatient.uid == foreign(CachePatientPrivateTags.uid)', viewonly=True, uselist=False)

	studies = Column(ARRAY(SqlString(64), as_tuple=False, dimensions=None, zero_indexes=False))
	studies_collection = relationship(
		'CacheStudy', back_populates='parent', overlaps='studies_collection,parent', viewonly=True)

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
	def index(cls, session, instance: ImagingPatient, commit=True, dcm_privatetags=None, **kwargs):
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

		# Add cached instance to session and (if indicated) commit
		session.add(ci)
		if commit:
			session.commit()

		return ci


class CacheStudy(CacheResourceMixin, DbBase):
	__tablename__ = 'sonador_cache_study'

	ts = Column(SqlDateTime(), nullable=True)
	modalities = Column(ARRAY(SqlString, as_tuple=False, dimensions=None, zero_indexes=False))
	series = Column(ARRAY(SqlString(64), as_tuple=False, dimensions=None, zero_indexes=False))

	parent_id = Column(
		SqlString(64), ForeignKey('sonador_cache_patient.uid', ondelete='CASCADE'), nullable=True)
	parent = relationship('CachePatient', overlaps='parent,studies_collection', viewonly=True)
	
	privatetags = relationship('CacheStudyPrivateTags', overlaps='study,privatetags', back_populates='study',
		primaryjoin='CacheStudy.uid == foreign(CacheStudyPrivateTags.uid)', viewonly=True, uselist=False)
	
	series_collection = relationship(
		'CacheSeries', back_populates='parent', overlaps='parent,series_collection', viewonly=True)

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
	def index(cls, session, instance: ImagingStudy, link=True, commit=True, dcm_privatetags=None, **kwargs):
		'''	Initialize a copy of the study in the index
		'''
		from .dcmext import CacheStudyPrivateTags

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

		# Add cached instance to session and (if indicated) commit
		session.add(ci)
		if commit:
			session.commit()

		return ci


class CacheSeries(CacheResourceMixin, DbBase):
	__tablename__ = 'sonador_cache_series'

	ts = Column(SqlDateTime(), nullable=True)
	instances = Column(ARRAY(SqlString(64), as_tuple=False, dimensions=None, zero_indexes=False))

	parent_id = Column(
		SqlString(64), ForeignKey('sonador_cache_study.uid', ondelete='CASCADE'), nullable=True)
	parent = relationship('CacheStudy', overlaps='series_collection,parent', viewonly=True)
	
	privatetags = relationship('CacheSeriesPrivateTags', overlaps='study,privatetags', back_populates='series',
		primaryjoin='CacheSeries.uid == foreign(CacheSeriesPrivateTags.uid)', 
		viewonly=True, uselist=False)

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

	@classmethod
	def _get_dcmtags(cls, instance, dcm_idx=0):
		'''	Retrieve DICOM tags from specified instance for series

			@input dcm_idx (int, default=0): index to use for retrieving DICOM instance
		'''
		if not len(instance.slices_collection):
			raise ValueError('Unable to retrieve DICOM tags, series=%s has no child instances.' % instance.pk)

		dcm0 = instance.slices_collection[dcm_idx]
		return dc0.tags

	@classmethod
	def index(cls, session, instance: ImagingSeries, link=True, commit=True, dcm_privatetags=None, **kwargs):
		'''	Initialize a copy of the series in the index
		'''
		from .dcmext import CacheSeriesPrivateTags

		ci = cls._init_cache_instance(session, instance)
		ci.instances = instance.slices
		ci.ts = instance.ts

		# Add database references
		if link:
			ci.parent_id = instance.study

		# Cache private tags
		if dcm_privatetags:
			pci = cls._init_privatetags(
				cls.privatetags_resource_model, session, instance, dcm_privatetags)
			session.add(pci)

		# Add cached instance to session and (if indicated) commit
		session.add(ci)
		if commit:
			session.commit()

		return ci
