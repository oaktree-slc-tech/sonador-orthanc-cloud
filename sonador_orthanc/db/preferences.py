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


class UserPreferences(DbBase):
	'''	User preferences JSON related to a User.
	'''
	__tablename__ = 'sonador_user_preferences'
	__table_args__ = { 'extend_existing': True }

	user = Column(SqlInteger)
	uid = Column(SqlString(64), primary_key=True, unique=True)
 
	data = Column(JSONB)
	