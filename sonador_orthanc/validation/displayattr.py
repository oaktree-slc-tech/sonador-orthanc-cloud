from typing import ClassVar, Optional

from pydantic import constr
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from client.utils.object import omit
from client.errors import ConfigurationError

from .. import apisettings as sonador_api
from ..db.helpers import normalize_dcm_code
from ..web.system import build_dcmtag_catalogue, catalogue_tagdef

from .base import OrthancBaseModelform, SonadorPolicyValidationMixin


def _validation_error(code, field, value, msg):
	return PydanticValidationError.from_exception_data(msg, line_errors=[
		InitErrorDetails(type=PydanticCustomError(code, msg), loc=(field,), input={ field: value }, msg=msg),
	])


def duplicate_code_error(raw_code, code, group):
	'''	The validation error for a code the group's collection already holds. Raised by the form's
		precheck and by the view when the unique constraint refuses an insert the precheck let through.
	'''
	return _validation_error(sonador_api.SONAODR_OBJECT_DUPLICATE_ERROR, 'Code', raw_code,
		'Tag "%s" is already part of the collection for group "%s"' % (code, group.name))


class DisplayAttributeValidationForm(SonadorPolicyValidationMixin, OrthancBaseModelform):
	'''	Form for validating a group's display attribute. `Code` must name a tag the imaging
		server indexes; `Tag` (keyword) and `Private` are filled from the catalogue, never from the
		request.
	'''
	Code: constr(strip_whitespace=True, min_length=8, max_length=9)
	Label: Optional[constr(strip_whitespace=True, max_length=256)] = None
	Tag: Optional[constr(strip_whitespace=True, max_length=128)] = None
	Private: bool = False

	db_fieldmap: ClassVar[dict] = {
		'Code': 'code',
		'Label': 'label',
		'Tag': 'keyword',
		'Private': 'private',
	}

	clean_omit_kwargs: ClassVar[tuple] = (
		'sonador_manager', 'session', 'create', 'update', 'model', 'obj', 'group',
		'request_user', 'request_user_groups')

	policy_feature: ClassVar[str] = 'display_attr'
	policy_feature_label: ClassVar[str] = 'Display attributes'
	policy_staff_manages: ClassVar[bool] = True

	@classmethod
	def clean(cls, *args, **kwargs):
		'''	Normalise the tag code, require it in the server's catalogue, refuse duplicates within
			the group, and keep the code of an existing entry fixed.
		'''
		sonador_manager = kwargs.get('sonador_manager')
		session = kwargs.get('session')
		model = kwargs.get('model')
		group = kwargs.get('group')
		obj = kwargs.get('obj')

		if not sonador_manager:
			raise ConfigurationError('Unable to validate display attribute, no Sonador manager provided to form')
		if session is None:
			raise ConfigurationError('Unable to validate display attribute, no database session provided to form')
		if not model:
			raise ConfigurationError('Unable to validate display attribute, no model class provided')
		if group is None:
			raise ConfigurationError('Unable to validate display attribute, no group provided')

		cls.validate_policy_write(sonador_manager, group, kwargs.get('request_user'), kwargs.get('request_user_groups'))

		raw_code = kwargs.get('Code')
		try:
			code = normalize_dcm_code(raw_code)
		except ValueError:
			raise _validation_error(sonador_api.SONAODR_OBJECT_INVALID_ERROR, 'Code', raw_code,
				'Tag "%s" is not a DICOM tag code. Use the form GGGG,EEEE.' % raw_code)

		if obj is not None and getattr(obj, 'code', None) and obj.code != code:
			raise _validation_error(sonador_api.SONAODR_OBJECT_INVALID_ERROR, 'Code', raw_code,
				'The tag code of an existing entry cannot be changed. Remove the entry and add the new tag.')

		tagdef = catalogue_tagdef(build_dcmtag_catalogue(sonador_manager), code)
		if tagdef is None:
			raise _validation_error(sonador_api.SONAODR_OBJECT_INVALID_ERROR, 'Code', raw_code,
				'Tag "%s" is not indexed by this imaging server' % code)

		duplicate = session.query(model).filter_by(group=int(group.pk), code=code).first()
		if duplicate is not None and (obj is None or duplicate.uid != obj.uid):
			raise duplicate_code_error(raw_code, code, group)

		data = omit(kwargs, cls.clean_omit_kwargs)
		data.update({
			'Code': code,
			'Label': kwargs.get('Label') or None,
			'Tag': tagdef.get('tag'),
			'Private': bool(tagdef.get('private')),
		})

		return super().clean(*args, **data)
