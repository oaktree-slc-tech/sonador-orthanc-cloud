'''	Data classes within Sonador/Orthanc that are responsible for validating the structure of data.
	Based on PyDantic with strong pattern influences from Django. Refer to https://docs.pydantic.dev.

	Within the module, pydantic data models are referred to as "forms" to maintain compatibility with the
	Django terminology and implement a classmethod based entry method called "clean" that can be used
	for data conversion and other actions.
'''
from .base import OrthancBaseForm
from .comments import CommentValidationForm
