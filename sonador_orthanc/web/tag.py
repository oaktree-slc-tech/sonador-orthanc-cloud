''' Web views which provide an API for managing imaging tags (coded concepts).

	Tags model the DICOM Code Sequence Macro (Code Value, Coding Scheme Designator,
	Coding Scheme Version, Code Meaning) and are scoped to a Sonador group.
'''
import json, uuid

import client.apisettings as gcapicodes
from client.errors import ConfigurationError
from client.utils.object import pick, omit
from client.errors import ConfigurationError, ResourceDoesNotExist, ClientOperationError

from sonador.serialization import SonadorJsonEncoder
from sonador import apisettings as sonador_api

from ..web.ext import ObjectManagementView, ObjectRestView
from ..web.ext.group import GroupChildManagementBaseView, GroupChildBaseRestView
from ..web.helpers import paginate_query_results
from ..web.secure_user import UserContextMixin, GroupLookupMixin

from ..db.tag import ImagingTag
from ..db.helpers import orthanc_tagjson

from ..validation.tag import TagValidationForm


class TagJsonMixin:
	'''	Mixin class which provides methods for serializing a series reviewer worklist item to JSON
	'''
	def orthanc_objectjson(self, t):
		'''	Serialize worklist to JSON, add user and group details to the response
		'''
		return orthanc_tagjson(t, group=self.group)


class TagItemManagementView(TagJsonMixin, UserContextMixin, GroupChildManagementBaseView):
	'''	Management endpoint which can be used to work with tag items
	'''
	sessionmaker = None
	model = ImagingTag
	modelform = TagValidationForm

	def modelform_kwargs(self, *args, **kwargs):
		'''	Add the request user to the form so the policy write rule can be applied
		'''
		form_kwargs = super().modelform_kwargs(*args, **kwargs)
		if getattr(self, 'user', None) is None:
			self.init_user_context(self.request, *args, **kwargs)
		form_kwargs.update({ 'request_user': self.user, 'request_user_groups': getattr(self, 'groups', None) })
		return form_kwargs

	def get_response_headers(self, response, status_code, method, *args, **kwargs):
		'''	Add operation and permissions headers to collection (GET) requests
		'''
		headers = super().get_response_headers(response, status_code, method, *args, **kwargs)
		if method == gcapicodes.HTTP_GET:

			# Initialize user context isa user data is not already available
			if getattr(self, 'user', None) is None:
				self.init_user_context(self.request, *args, **kwargs)

			_user, _group = self.user, self.get_group(*args, **kwargs)
			_group_acl = self.sonador_manager.get_internal_imageserver().fetch_acl().get_group_acl(_group.pk)

			# Add Tag Group Permissions, as the write rule on the form enforces them
			headers[sonador_api.SONADOR_PERMISSIONS_HEADER] = json.dumps(
				TagValidationForm.policy_permissions(_group_acl, _user), cls=SonadorJsonEncoder)

			# Ensure that the headers are visible in the response
			# Ensure that the headers are visible in the response
			exposed_headers = headers.get(gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER) \
				or headers.get(gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER.lower()) \
				or headers.get(gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER.upper()) \
				or []

			exposed_headers.append(sonador_api.SONADOR_PERMISSIONS_HEADER)
			headers[gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER] = ', '.join(exposed_headers)

		return headers


class TagItemRestView(TagJsonMixin, UserContextMixin, GroupChildBaseRestView):
	'''	REST endpoint which can be used to retrieve details for, update, and delete series reviewer worklist items
	'''
	model = ImagingTag
	modelform = TagValidationForm

	def modelform_kwargs(self, **kwargs):
		'''	Add the request user to the form so the policy write rule can be applied
		'''
		form_kwargs = super().modelform_kwargs(**kwargs)
		if getattr(self, 'user', None) is None:
			self.init_user_context(self.request, **kwargs)
		form_kwargs.update({ 'request_user': self.user, 'request_user_groups': getattr(self, 'groups', None) })
		return form_kwargs

	def validate_delete(self, session, obj, *args, **kwargs):
		'''	Removal follows the same policy rule as a create or update
		'''
		if getattr(self, 'user', None) is None:
			self.init_user_context(self.request, *args, **kwargs)
		self.modelform.validate_policy_write(self.sonador_manager, self.get_group(*args, **kwargs),
			self.user, getattr(self, 'groups', None))

	def err_404(self, err, *args, **kwargs):
		'''	Create 404 error message which includes the UID of the group
		'''
		gid = self.get_group_uid(*args, **kwargs)
		uid = self.get_object_uid(*args, **kwargs)

		return 'Unable to retrieve Tag=%s for Group=%s. Tag instance does not exist.' \
			% (uid or '(none)', gid or '(none)')
