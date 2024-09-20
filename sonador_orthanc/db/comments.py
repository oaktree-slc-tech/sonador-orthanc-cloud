import abc

from sqlalchemy import Column, ForeignKey, BigInteger as SqlBigInteger, Integer as SqlInteger, String as SqlString, \
	DateTime as SqlDateTime, Boolean as SqlBoolean, Text as SqlText, event
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type
from sqlalchemy.orm import relationship

from client.utils.decorators import classproperty

from .base import DbBase, AutoDbBase
from .cache import CacheResourceDbPropertiesMixin
from .helpers import set_ctime, set_mtime

from datetime import datetime


class ImagingSeriesComment(DbBase):
	'''	Comment related to an imaging series within Orthanc
	'''
	__tablename__ = 'sonador_series_comment'
	__table_args__ = { 'extend_existing': True }

	uid = Column(SqlString(64), primary_key=True, unique=True)
	series_id = Column(SqlString(64))
	series = relationship('CacheSeries', back_populates='comments',
		primaryjoin='foreign(ImagingSeriesComment.series_id) == CacheSeries.uid',
		viewonly=True, uselist=False)
	user = Column(SqlBigInteger(), nullable=True)

	# Creation and modification times
	ctime = Column(SqlDateTime())
	mtime = Column(SqlDateTime())

	text = Column(SqlText())
	orthanc = Column(mutable_json_type(dbtype=JSONB, nested=True))

	@classproperty
	def resource_foreignkey_attr(cls):
		'''	Foreign key column name that maps the comment to the parent resource
		'''
		return 'series_id'

	@classproperty
	def type(cls):
		return 'Series Comment'

	@classproperty
	def resource_cachemodel(cls):
		from .cache import CacheSeries
		return CacheSeries


class ImagingStudyComment(DbBase):
	'''	Comment related to an imaging study within Orthanc
	'''
	__tablename__ = 'sonador_study_comment'
	__table_args__ = { 'extend_existing': True }

	uid = Column(SqlString(64), primary_key=True, unique=True)
	study_id = Column(SqlString(64))
	study = relationship('CacheStudy', back_populates='comments',
		primaryjoin='foreign(ImagingStudyComment.study_id) == CacheStudy.uid',
		viewonly=True, uselist=False)
	user = Column(SqlBigInteger(), nullable=True)

	# Creation and modification times
	ctime = Column(SqlDateTime())
	mtime = Column(SqlDateTime())

	text = Column(SqlText())
	orthanc = Column(mutable_json_type(dbtype=JSONB, nested=True))

	@classproperty
	def resource_foreignkey_attr(cls):
		'''	Foreign key column name that maps the comment to the parent resource
		'''
		return 'study_id'

	@classproperty
	def type(cls):
		return 'Study Comment'

	@classproperty
	def resource_cachemodel(cls):
		from .cache import CacheStudy
		return CacheStudy


# Database events


# Series Comment
event.listens_for(ImagingSeriesComment, 'before_insert')(set_ctime)
event.listens_for(ImagingSeriesComment, 'before_update')(set_mtime)


# Study Comment
event.listens_for(ImagingStudyComment, 'before_insert')(set_ctime)
event.listens_for(ImagingStudyComment, 'before_update')(set_mtime)
