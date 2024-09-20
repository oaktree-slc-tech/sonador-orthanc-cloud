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


class ImagingTag(DbBase):
	'''	Comment related to an imaging series within Orthanc
	'''
	__tablename__ = 'sonador_tag'
	__table_args__ = { 'extend_existing': True }

	group = Column(SqlInteger)
	uid = Column(SqlString(64), primary_key=True, unique=True)

	value = Column(SqlString(128))
	meaning = Column(SqlString(256))
	scheme_designator = Column(SqlString(64))
	scheme_version = Column(SqlString(64))
	