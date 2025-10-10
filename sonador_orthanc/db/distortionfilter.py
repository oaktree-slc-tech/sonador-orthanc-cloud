from sqlalchemy import Column, ForeignKey, Integer as SqlInteger, BigInteger as SqlBigInteger, String as SqlString, \
	DateTime as SqlDateTime, Boolean as SqlBoolean, Text as SqlText, event

from client.utils.decorators import classproperty

from .base import DbBase
from .helpers import set_ctime, set_mtime


class DistortionFilterDevice(DbBase):
	'''	Device list for Sonador distortion filter
	'''
	__tablename__ = 'sonador_distortionfilter_devices'
	__table_args__ = { 'extend_existing': True }

	uid = Column(SqlString(64), primary_key=True, unique=True)
	group = Column(SqlBigInteger)
	
	# Creation and modification times
	ctime = Column(SqlDateTime())
	mtime = Column(SqlDateTime())

	# Imaging center, manufacturer
	institution_name = Column(SqlString(64))
	manufacturer = Column(SqlString(64))
	manufacturer_modelname = Column(SqlString(64))
	software_versions = Column(SqlString(64))

	# DICOM tag information
	dcm_tag_name = Column(SqlString(64))
	dcm_tag_value = Column(SqlString(64))

	@classproperty
	def principal_foreignkey_attr(cls):
		'''	Foreign key column that maps to the principal (group) associated with the distortion filter device
		'''
		return 'group'

	@classproperty
	def type(self):
		return 'Distortion Filter Device'


event.listens_for(DistortionFilterDevice, 'before_insert')(set_ctime)
event.listens_for(DistortionFilterDevice, 'before_update')(set_mtime)
