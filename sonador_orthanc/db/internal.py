from sqlalchemy import Column, Integer as SqlInteger, String as SqlString

from .base import AutoDbBase


ORTHANCDB_PATIENT_TYPE = 0
ORTHANCDB_STUDY_TYPE = 1
ORTHANCDB_SERIES_TYPE = 2


class Resource(AutoDbBase):
	__tablename__ = 'resources'


class Changes(AutoDbBase):
	__tablename__ = 'changes'