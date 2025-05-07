import abc

from sqlalchemy import Column, ForeignKey, Integer as SqlInteger, String as SqlString, \
	DateTime as SqlDateTime, Boolean as SqlBoolean, PrimaryKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type
from sqlalchemy.orm import relationship

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_REPORT
from sonador.imaging.orthanc.base import ImagingSeries, ImagingStudy, ImagingPatient

from .base import DbBase, AutoDbBase
from .cache import CacheResourceDbPropertiesMixin, CachePatient, CacheStudy, CacheSeries


class CacheExtendedDcmBase(CacheResourceDbPropertiesMixin):
	__table_args__ = { 'extend_existing': True }



class CachePatientPrivateTags(CacheExtendedDcmBase, DbBase):
	''' Patient cache model that can be used to store patient private tags.
	'''
	__tablename__ = 'sonador_cache_patient_private'

	patient = relationship('CachePatient', 
		back_populates='privatetags', primaryjoin='foreign(CachePatientPrivateTags.uid) == CachePatient.uid', 
		viewonly=True, uselist=False)


class CacheStudyPrivateTags(CacheExtendedDcmBase, DbBase):
	'''	Study cache model that can be used to store study private tags.
	'''
	__tablename__ = 'sonador_cache_study_private'

	study = relationship('CacheStudy',
		back_populates='privatetags', primaryjoin='foreign(CacheStudyPrivateTags.uid) == CacheStudy.uid', 
		viewonly=True, uselist=False)


class CacheSeriesPrivateTags(CacheExtendedDcmBase, DbBase):
	'''	Series cache model that can be used to store series private tags. 
	'''
	__tablename__ = 'sonador_cache_series_private'

	series = relationship('CacheSeries',
		back_populates='privatetags', primaryjoin='foreign(CacheSeriesPrivateTags.uid) == CacheSeries.uid', 
		viewonly=True, uselist=False)


class CacheInstancePrivateTags(CacheExtendedDcmBase, DbBase):
	'''	Instance cache model that can be used to store instance private tags
	'''
	__tablename__ = 'sonador_cache_instance_private'

	instance = relationship('CacheInstance',
		back_populates='privatetags', primaryjoin='foreign(CacheInstancePrivateTags.uid) == CacheInstance.uid',
		viewonly=True, uselist=False)



class CacheDatetimePropertiesMixin:
	'''	Mixin class providing timestamp fields for cache date/time tables
	'''
	__table_args__ = (UniqueConstraint('uid', 'date_tag', 'time_tag'), { 'extend_existing': True })

	uid = Column(SqlString(64), primary_key=True)
	date_tag = Column(SqlString(64), primary_key=True)
	time_tag = Column(SqlString(64), primary_key=True)

	ts = Column(SqlDateTime())



class CachePatientDatetime(CacheDatetimePropertiesMixin, DbBase):
	'''	Patient cache model that can be used to store and query DICOM timestamps.
	'''
	__tablename__ = 'sonador_cache_patient_datetime'

	patient = relationship('CachePatient', back_populates='timestamp_tags', 
		primaryjoin='foreign(CachePatientDatetime.uid) == CachePatient.uid', viewonly=True)


class CacheStudyDatetime(CacheDatetimePropertiesMixin, DbBase):
	'''	Study cache model that can be used to store and query DICOM timestamps.
	'''
	__tablename__ = 'sonador_cache_study_datetime'

	study = relationship('CacheStudy', back_populates='timestamp_tags',
		primaryjoin='foreign(CacheStudyDatetime.uid) == CacheStudy.uid', viewonly=True)


class CacheSeriesDatetime(CacheDatetimePropertiesMixin, DbBase):
	'''	Series cache model that can be used to store and query DICOM timestamps
	'''
	__tablename__ = 'sonador_cache_series_datetime'

	series = relationship('CacheSeries', back_populates='timestamp_tags',
		primaryjoin='foreign(CacheSeriesDatetime.uid) == CacheSeries.uid', viewonly=True)


class CacheInstanceDatetime(CacheDatetimePropertiesMixin, DbBase):
	'''	Instance cache model that can be used to store and query DICOM timestamps
	'''
	__tablename__ = 'sonador_cache_instance_datetime'

	instance = relationship('CacheInstance', back_populates='timestamp_tags',
		primaryjoin='foreign(CacheInstanceDatetime.uid) == CacheInstance.uid', viewonly=True)
