''' Validation for the optional worklist `Procedure` block.

	The `Procedure` block rides alongside the existing `State`, `Meta`, and `Comment`
	fields of a worklist create/update request and carries structured Requested Procedure
	and/or Performed Procedure data. Attribute keys and structure mirror the DICOM Requested
	Procedure module, Performed Procedure Step, and Code Sequence Macro (refer to
	orthanc-sonador#54 section 6.2).

	Per FR-3 each procedure facet is valid when it carries at least one content-bearing
	attribute (a coded concept and/or a free-text description); code-only and description-only
	facets are both first-class. Completely empty facets are rejected.
'''
from typing import Optional, List, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError as PydanticValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from .. import apisettings as sonador_api


# Server-owned keys inside the worklist item's `orthanc` JSONB (surfaced to clients as `Meta`).
# Clients may never write these through the general `Meta` channel; procedure writes come only
# through the validated `Procedure` block (FR-7).
RESERVED_META_KEYS = ('RequestedProcedure', 'PerformedProcedure', 'ReviewHistory')


class CodeSequenceItem(BaseModel):
	'''	A single item of a DICOM Code Sequence Macro (0008,0100)/(0008,0102)/(0008,0103)/(0008,0104).
		Maps to the group's existing `ImagingTag` columns.
	'''
	model_config = ConfigDict(extra='forbid')

	CodeValue: str							# (0008,0100) -> ImagingTag.value
	CodingSchemeDesignator: str				# (0008,0102) -> ImagingTag.scheme_designator
	CodingSchemeVersion: Optional[str] = None	# (0008,0103) -> ImagingTag.scheme_version
	CodeMeaning: str						# (0008,0104) -> ImagingTag.meaning


class RequestedProcedureModel(BaseModel):
	'''	DICOM Requested Procedure module (what the requester wants assessed).
	'''
	model_config = ConfigDict(extra='forbid')

	RequestedProcedureID: Optional[str] = None					# (0040,1001) SH
	RequestedProcedureDescription: Optional[str] = None			# (0032,1060) LO
	RequestedProcedureCodeSequence: Optional[List[CodeSequenceItem]] = None	# (0032,1064) SQ
	RequestedProcedurePriority: Optional[str] = None			# (0040,1003) SH
	ReasonForTheRequestedProcedure: Optional[str] = None		# (0040,1002) LO

	# Content-bearing attributes: a facet is valid when at least one of these is populated.
	content_attrs: ClassVar[tuple] = (
		'RequestedProcedureDescription', 'RequestedProcedureCodeSequence', 'ReasonForTheRequestedProcedure')


class PerformedProcedureModel(BaseModel):
	'''	DICOM Performed Procedure Step (what the reviewer actually did).
	'''
	model_config = ConfigDict(extra='forbid')

	PerformedProcedureStepID: Optional[str] = None				# (0040,0253) SH
	PerformedProcedureStepDescription: Optional[str] = None		# (0040,0254) LO
	ProcedureCodeSequence: Optional[List[CodeSequenceItem]] = None			# (0008,1032) SQ
	PerformedProtocolCodeSequence: Optional[List[CodeSequenceItem]] = None	# (0040,0260) SQ
	PerformedProcedureStepStatus: Optional[str] = None			# (0040,0252) CS
	PerformedProcedureStepStartDateTime: Optional[str] = None	# (0040,0244)/(0040,0245)
	PerformedProcedureStepEndDateTime: Optional[str] = None		# (0040,0250)/(0040,0251)

	# Content-bearing attributes: a facet is valid when at least one of these is populated.
	content_attrs: ClassVar[tuple] = (
		'PerformedProcedureStepDescription', 'ProcedureCodeSequence', 'PerformedProtocolCodeSequence')


# Supported facets of the Procedure block mapped to their validation models
PROCEDURE_FACETS: dict = {
	'RequestedProcedure': RequestedProcedureModel,
	'PerformedProcedure': PerformedProcedureModel,
}


def _raise_procedure_error(loc, msg, value=None):
	'''	Raise a PydanticValidationError in the Sonador API format used across the plugin.
	'''
	err = PydanticValidationError.from_exception_data(msg, line_errors=[
		InitErrorDetails(
			type=PydanticCustomError(sonador_api.SONAODR_OBJECT_INVALID_ERROR, msg),
			loc=tuple(loc), input=value, msg=msg),
	])
	raise err


class ProcedureValidationForm:
	'''	Validate the optional `Procedure` block of a worklist create/update request.

		The form is intentionally structural: it validates the shape of the Requested/Performed
		procedure facets (FR-1/FR-2/FR-3) and returns a normalized dict of server-owned procedure
		data. Plugin metadata (`RequestedBy`, `RequestedDateTime`, `PerformedBy`) is applied by the
		view, which holds the request user context.
	'''
	facets: ClassVar[dict] = PROCEDURE_FACETS

	@classmethod
	def _facet_has_content(cls, model, data):
		'''	FR-3: a facet is valid when at least one content-bearing attribute is populated.
		'''
		for attr in getattr(model, 'content_attrs', tuple()):
			value = data.get(attr)
			if value not in (None, '', [], {}):
				return True
		return False

	@classmethod
	def clean(cls, procedure, **kwargs):
		'''	Validate and normalize the provided `Procedure` block.

			@input procedure (dict): value of the request `Procedure` key

			@raises PydanticValidationError: if the block is malformed or a facet is empty
			@returns dict: normalized {facet_key: {..procedure attrs..}} with None values omitted
		'''
		if not isinstance(procedure, dict):
			_raise_procedure_error(('Procedure',), 'Procedure block must be an object.', procedure)

		# Reject unknown top-level facets to keep the namespace tight
		unknown = set(procedure.keys()).difference(cls.facets.keys())
		if unknown:
			_raise_procedure_error(('Procedure',),
				'Unknown procedure facet(s): %s. Supported facets: %s.' % (
					', '.join(sorted(unknown)), ', '.join(cls.facets.keys())),
				procedure)

		validated = {}
		for facet_key, model in cls.facets.items():
			facet_input = procedure.get(facet_key)
			if facet_input is None:
				continue

			if not isinstance(facet_input, dict):
				_raise_procedure_error(('Procedure', facet_key), '%s must be an object.' % facet_key, facet_input)

			# Structural/type validation (raises PydanticValidationError on malformed input)
			instance = model(**facet_input)
			data = instance.model_dump(exclude_none=True)

			# FR-3: reject a completely empty facet
			if not cls._facet_has_content(model, data):
				_raise_procedure_error(('Procedure', facet_key),
					'%s must include at least one of a coded concept or a description.' % facet_key,
					facet_input)

			validated[facet_key] = data

		return validated
