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

	@property
	def type(self):
		return IMAGING_SERVER_RESOURCE_PATIENT


class CacheStudyPrivateTags(CacheExtendedDcmBase, DbBase):
	'''	Study cache model that can be used to store study private tags.
	'''
	__tablename__ = 'sonador_cache_study_private'

	study = relationship('CacheStudy',
		back_populates='privatetags', primaryjoin='foreign(CacheStudyPrivateTags.uid) == CacheStudy.uid', 
		viewonly=True, uselist=False)

	@property
	def type(self):
		return IMAGING_SERVER_RESOURCE_STUDY


class CacheSeriesPrivateTags(CacheExtendedDcmBase, DbBase):
	'''	Series cache model that can be used to store series private tags. 
	'''
	__tablename__ = 'sonador_cache_series_private'

	series = relationship('CacheSeries',
		back_populates='privatetags', primaryjoin='foreign(CacheSeriesPrivateTags.uid) == CacheSeries.uid', 
		viewonly=True, uselist=False)

	@property
	def type(self):
		return IMAGING_SERVER_RESOURCE_SERIES
