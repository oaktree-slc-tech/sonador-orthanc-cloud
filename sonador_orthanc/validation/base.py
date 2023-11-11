import abc
from typing import ClassVar
from pydantic import BaseModel as BaseValidationModel, constr


class OrthancBaseForm(BaseValidationModel, abc.ABC):
	'''	Base class for Orthanc data validation forms. Builds on top of pydantic.BaseModel
		with pattern inspirations from Django forms. The entrypoint to a form instance is
		intended to be the "clean" method, which should be used for performing conversion
		and cleaning operations.
	'''
	db_fieldmap: ClassVar[dict] = {}

	@classmethod
	def clean(cls, *args, **kwargs):
		''' Perform data conversion and field validation. All input arguments and keyword arguments
			should be converted to the proper format to be used for initializing the base form
			instance (OrthancBaseForm inherits from pydantic.BaseModel).

			Any fields that are not in the proper form will trigger a validation error.
			Refer to https://docs.pydantic.dev/latest/api/base_model/.

			@returns instance of the form class
		'''
		return cls(*args, **kwargs)

	def save(self, session, dbmodel, *args, commit=True, **kwargs):
		''' Persist data from the form to the provided model model instance

			@returns dbmodel
		'''
		# Iterate through fields defined by the form and set the associated value
		# on the database instance.
		for fname, fvalue in self.dict().items():
			setattr(dbmodel, self.db_fieldmap[fname] if fname in self.db_fieldmap else fname, fvalue)

		# Commit model to session
		if commit:
			session.add(dbmodel)
			session.commit()

		return dbmodel
