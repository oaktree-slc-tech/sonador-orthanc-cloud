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


class CacheResourceMixin:
	'''	Mixin class providing Orthanc identifiers and cache fields for an Orthanc
		MainDicomTags response.
	'''
	__table_args__ = { 'extend_existing': True }

	uid = Column(SqlString(64), primary_key=True, unique=True)
	orthanc = Column(mutable_json_type(dbtype=JSONB, nested=True))
	mtime = Column(SqlDateTime(), nullable=True)
	stable = Column(SqlBoolean(), nullable=True)

	@classmethod
	def _init_cache_instance(cls, session, rinstance):
		'''	Initialize (or retrieve) the cached version of the resource
		'''
		ci = session.query(cls).filter_by(uid=rinstance.pk).first()
		if not ci:
			ci = cls(uid=rinstance.pk)

		# Copy Orthanc DICOM properties to cache instance
		ci.orthanc = rinstance.dicomdata
		ci.stable = rinstance.stable
		ci.mtime = rinstance.lastupdate
		return ci

	@classmethod
	@abc.abstractmethod
	def index(self, session, instance, *args, link=True, commit=True, **kwargs):
		''' Initialize a copy of the resource in the index.
		'''


class CachePatient(CacheResourceMixin, DbBase):
	__tablename__ = 'sonador_cache_patient'

	birth_date = Column(SqlDateTime(), nullable=True)

	studies = Column(ARRAY(SqlString(64), as_tuple=False, dimensions=None, zero_indexes=False))
	studies_collection = relationship(
		'CacheStudy', back_populates='parent', overlaps='studies_collection,parent', viewonly=True)

	@property
	def type(self):
		return IMAGING_SERVER_RESOURCE_PATIENT

	@classmethod
	def index(cls, session, instance: ImagingPatient, commit=True, **kwargs):
		'''	Initialize a copy of the patient in the index
		'''
		ci = cls._init_cache_instance(session, instance)
		ci.studies = instance.studies
		ci.birth_date = instance.birth_date

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
	
	series_collection = relationship(
		'CacheSeries', back_populates='parent', overlaps='parent,series_collection', viewonly=True)

	@property
	def type(self):
		return IMAGING_SERVER_RESOURCE_STUDY

	@classmethod
	def index(cls, session, instance: ImagingStudy, link=True, commit=True, **kwargs):
		'''	Initialize a copy of the study in the index
		'''
		ci = cls._init_cache_instance(session, instance)
		ci.series = getattr(instance, 'series', [])
		ci.modalities = list(set([sx.modality for sx in instance.series_collection if sx.modality]))
		ci.ts = instance.ts

		# Add database references
		if link:
			ci.parent_id = instance.patient

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

	@property
	def type(self):
		return IMAGING_SERVER_RESOURCE_SERIES

	@classmethod
	def index(cls, session, instance: ImagingSeries, link=True, commit=True, **kwargs):
		'''	Initialize a copy of the series in the index
		'''
		ci = cls._init_cache_instance(session, instance)
		ci.instances = instance.slices
		ci.ts = instance.ts

		# Add database references
		if link:
			ci.parent_id = instance.study

		# Add cached instance to session and (if indicated) commit
		session.add(ci)
		if commit:
			session.commit()

		return ci
