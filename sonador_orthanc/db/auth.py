import abc

from sqlalchemy import Column, ForeignKey, Integer as SqlInteger, String as SqlString, \
	DateTime as SqlDateTime, Boolean as SqlBoolean, Text as SqlText, event, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type
from sqlalchemy.orm import relationship

from client.utils.decorators import classproperty
from client.utils.object import gextend

from .base import DbBase, AutoDbBase
from .cache import CacheResourceDbPropertiesMixin
from .helpers import set_ctime, set_mtime

from datetime import datetime


class UserAuthorizationPropertiesMixin:
	''' Mixin class providing common properties for user Orthanc resource authorizations.
	'''
	__table_args__ = (UniqueConstraint('user', 'resource'), { 'extend_existing': True })

	uid = Column(SqlString(64), primary_key=True, unique=True)
	user = Column(SqlInteger)
	resource = Column(SqlString(64))

	# Creation and modification times
	ctime = Column(SqlDateTime())
	mtime = Column(SqlDateTime())

	@classproperty
	def principal_foreignkey_attr(cls):
		'''	Foreign key column that maps to the principal (user) associated with the policy
		'''
		return 'user'


class GroupAuthorizationPropertiesMixin:
	''' Mixin class providing common properties for group Orthanc resource authorizations.
	'''
	__table_args__ = (UniqueConstraint('group', 'resource'), { 'extend_existing': True })

	uid = Column(SqlString(64), primary_key=True, unique=True)
	group = Column(SqlInteger)
	resource = Column(SqlString(64))

	# Creation and modification times
	ctime = Column(SqlDateTime())
	mtime = Column(SqlDateTime())

	@classproperty
	def principal_foreignkey_attr(cls):
		'''	Foreign key column that maps to the principal (group) associated with the policy
		'''
		return 'group'


class AuthorizationPermissionMixin:
	''' Mixin class providing resource permissions.

		@column view (bool): provides permission to view a resource
		@column modify (bool): provides permission to change properties or attributes of a resource
		@column remove (bool): provides permission to remove the resource from the server
		@column acl (bool): provides permission to view or modify the ACL grants associated with the resource

	'''
	view = Column(SqlBoolean())
	modify = Column(SqlBoolean())
	remove = Column(SqlBoolean())
	acl = Column(SqlBoolean())


class CommentsPermissionMixin:
	''' Mixin class providing resource permissions for comments.

		@column comment_edit (bool): provides permission to view/edit a comment
		@column comment_view (bool): provides permission to view a comment, when comment_edit is True
			it takes precedence.
	'''
	comment_edit = Column(SqlBoolean())
	comment_view = Column(SqlBoolean())    


class UserPatientAuth(UserAuthorizationPropertiesMixin, AuthorizationPermissionMixin, DbBase):
	''' patient table holding the uid's of all users with access to that patient
	'''
	__tablename__ = 'sonador_auth_user_patient'

	patient = relationship('CachePatient', back_populates='auth_user',
		primaryjoin='foreign(UserPatientAuth.resource) == CachePatient.uid',
		viewonly=True, uselist=False)

	@classproperty
	def type(cls):
		return 'User ACL for Patient Resources'

	@classproperty
	def resource_cachemodel(cls):
		from .cache import CachePatient
		return CachePatient


class GroupPatientAuth(GroupAuthorizationPropertiesMixin, AuthorizationPermissionMixin, DbBase):
	''' patient table holding the uid's of all groups with access to that patient
	'''
	__tablename__ = 'sonador_auth_group_patient'

	patient = relationship('CachePatient', back_populates='auth_group',
		primaryjoin='foreign(GroupPatientAuth.resource) == CachePatient.uid',
		viewonly=True, uselist=False)

	@classproperty
	def type(cls):
		return 'Group ACL for Patient Resources'

	@classproperty
	def resource_cachemodel(cls):
		from .cache import CachePatient
		return CachePatient
	

class UserStudyAuth(UserAuthorizationPropertiesMixin, AuthorizationPermissionMixin, DbBase):
	''' study table holding the uid's of all users with access to that study
	'''
	__tablename__ = 'sonador_auth_user_study'

	study = relationship('CacheStudy', back_populates='auth_user',
		primaryjoin='foreign(UserStudyAuth.resource) == CacheStudy.uid',
		viewonly=True, uselist=False)

	@classproperty
	def type(cls):
		return 'User ACL for Study Resources'

	@classproperty
	def resource_cachemodel(cls):
		from .cache import CacheStudy
		return CacheStudy
	

class GroupStudyAuth(GroupAuthorizationPropertiesMixin, AuthorizationPermissionMixin, DbBase):
	''' study table holding the uid's of all groups with access to that study
	'''
	__tablename__ = 'sonador_auth_group_study'

	study = relationship('CacheStudy', back_populates='auth_group',
		primaryjoin='foreign(GroupStudyAuth.resource) == CacheStudy.uid',
		viewonly=True, uselist=False)

	@classproperty
	def type(cls):
		return 'Group ACL for Study Resources'

	@classproperty
	def resource_cachemodel(cls):
		from .cache import CacheStudy
		return CacheStudy


class UserSeriesAuth(
		CommentsPermissionMixin, UserAuthorizationPropertiesMixin, AuthorizationPermissionMixin, DbBase):
	''' series table holding the uid's of all users with access to that series
	'''
	__tablename__ = 'sonador_auth_user_series'

	series = relationship('CacheSeries', back_populates='auth_user',
		primaryjoin='foreign(UserSeriesAuth.resource) == CacheSeries.uid',
		viewonly=True, uselist=False)

	@classproperty
	def type(cls):
		return 'User ACL for Series Resources'

	@classproperty
	def resource_cachemodel(cls):
		from .cache import CacheSeries
		return CacheSeries


class GroupSeriesAuth(
		CommentsPermissionMixin, GroupAuthorizationPropertiesMixin, AuthorizationPermissionMixin, DbBase):
	''' series table holding the uid's of all groups with access to that series
	'''
	__tablename__ = 'sonador_auth_group_series'

	series = relationship('CacheSeries', back_populates='auth_group',
		primaryjoin='foreign(GroupSeriesAuth.resource) == CacheSeries.uid',
		viewonly=True, uselist=False)

	@classproperty
	def type(cls):
		return 'Group ACL for Series Resources'

	@classproperty
	def resource_cachemodel(cls):
		from .cache import CacheSeries
		return CacheSeries



# Process database events
event.listens_for(UserPatientAuth, 'before_insert')(set_ctime)
event.listens_for(UserPatientAuth, 'before_update')(set_mtime)

event.listens_for(GroupPatientAuth, 'before_insert')(set_ctime)
event.listens_for(GroupPatientAuth, 'before_update')(set_mtime)

event.listens_for(UserStudyAuth, 'before_insert')(set_ctime)
event.listens_for(UserStudyAuth, 'before_update')(set_mtime)

event.listens_for(GroupStudyAuth, 'before_insert')(set_ctime)
event.listens_for(GroupStudyAuth, 'before_update')(set_mtime)

event.listens_for(UserSeriesAuth, 'before_insert')(set_ctime)
event.listens_for(UserSeriesAuth, 'before_update')(set_mtime)

event.listens_for(GroupSeriesAuth, 'before_insert')(set_ctime)
event.listens_for(GroupSeriesAuth, 'before_update')(set_mtime)
