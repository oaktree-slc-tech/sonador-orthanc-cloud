from wtfroms_alchemy import ModelForm

from ..db.worklist import ProcedureStep


class ProcedureStepForm(ModelForm):
	'''	Model form for validating API data for the unified procedure step API
	'''
	class Meta:
		model = ProcedureStep

	