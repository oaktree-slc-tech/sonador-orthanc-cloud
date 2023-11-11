import abc

from sqlalchemy import Column, ForeignKey, Integer as SqlInteger, String as SqlString, \
	DateTime as SqlDateTime, Boolean as SqlBoolean, Text as SqlText, event
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type
from sqlalchemy.orm import relationship

from client.utils.decorators import classproperty

from .base import DbBase, AutoDbBase
from .cache import CacheResourceDbPropertiesMixin

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

	# Creation and modification times
	ctime = Column(SqlDateTime())
	mtime = Column(SqlDateTime())

	text = Column(SqlText())

	@classproperty
	def resource_foreignkey_attr(cls):
		'''	Foreign key column name that maps the comment to the parent resource
		'''
		return 'series_id'


@event.listens_for(ImagingSeriesComment, 'before_insert')
def set_ctime(mapper, connection, target):
    current_time = datetime.utcnow()
    target.ctime = current_time
    target.mtime = current_time


@event.listens_for(ImagingSeriesComment, 'before_update')
def set_mtime(mapper, connection, target):
    target.mtime = datetime.utcnow()
