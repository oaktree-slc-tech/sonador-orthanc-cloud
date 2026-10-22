'''	Pydantic validation for the tag `Meta` document, `{ 'DisplayAttributes': { 'Color': {...} } }`.
	Color normalization comes from Sonador IO; this module adds the API's schema: unknown keys
	and blank colors are refused, and cleared (null) colors are pruned from the stored document.
'''
from typing import Optional
from typing_extensions import Annotated

from pydantic import BaseModel, ConfigDict
from pydantic.functional_validators import BeforeValidator

from sonador.apisettings.display_attrs import normalize_hex_color


def _prune(value):
	if isinstance(value, dict):
		pruned = { k: _prune(v) for k, v in value.items() }
		return { k: v for k, v in pruned.items() if v not in (None, {}) }
	return value


HexColor = Annotated[Optional[str], BeforeValidator(normalize_hex_color)]


class DisplayColorForm(BaseModel):
	'''	Colors applied to the elements of a rendered tag
	'''
	model_config = ConfigDict(extra='forbid')

	Background: HexColor = None
	Border: HexColor = None
	Value: HexColor = None
	Meaning: HexColor = None


class DisplayAttributesForm(BaseModel):
	model_config = ConfigDict(extra='forbid')

	Color: Optional[DisplayColorForm] = None


class DisplayMetaForm(BaseModel):
	'''	`Meta` document which carries display attributes. Unknown keys are rejected.
	'''
	model_config = ConfigDict(extra='forbid')

	DisplayAttributes: Optional[DisplayAttributesForm] = None

	def to_json(self):
		'''	JSON stored for the document: unset and cleared (null) values are dropped, and an
			empty document is stored as `None`.
		'''
		return _prune(self.model_dump()) or None
