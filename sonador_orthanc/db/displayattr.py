from sqlalchemy import Column, BigInteger as SqlBigInteger, String as SqlString, DateTime as SqlDateTime, \
	Boolean as SqlBoolean, UniqueConstraint, event

from client.utils.decorators import classproperty

from .base import DbBase
from .helpers import set_ctime, set_mtime


class DisplayAttribute(DbBase):
	'''	DICOM attribute a group curates for the viewer's corner overlay (a Display Attribute). `code` is the
		canonical `GGGG,EEEE` form and must exist in the server's indexed tag catalogue.
	'''
	__tablename__ = 'sonador_display_attribute'
	__table_args__ = (
		UniqueConstraint('group', 'code', name='uq_sonador_display_attribute_group_code'),
		{ 'extend_existing': True },
	)

	uid = Column(SqlString(64), primary_key=True, unique=True)
	group = Column(SqlBigInteger, nullable=False)

	# Creation and modification times
	ctime = Column(SqlDateTime())
	mtime = Column(SqlDateTime())

	# Tag definition
	code = Column(SqlString(9), nullable=False)
	keyword = Column(SqlString(128))
	label = Column(SqlString(256))
	private = Column(SqlBoolean, nullable=False, default=False)

	@classproperty
	def principal_foreignkey_attr(cls):
		'''	Foreign key column that maps to the principal (group) associated with the tag
		'''
		return 'group'

	@classproperty
	def type(self):
		return 'Display Attribute'


event.listens_for(DisplayAttribute, 'before_insert')(set_ctime)
event.listens_for(DisplayAttribute, 'before_update')(set_mtime)
