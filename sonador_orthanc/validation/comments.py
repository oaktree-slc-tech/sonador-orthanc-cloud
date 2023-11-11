from typing import ClassVar
from pydantic import constr

from .base import OrthancBaseForm


class CommentValidationForm(OrthancBaseForm):
	''' Validation model for validating the structure of resource comments
	'''
	Text: constr(strip_whitespace=True, min_length=1)

	db_fieldmap: ClassVar[dict] = { 'Text': 'text' }
	