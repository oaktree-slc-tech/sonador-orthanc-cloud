''' Web views which provide an API compatible with the Unified Procedure Step DICOM specification.
	UPS facilitates the retrieval of scheduled procedures via worklist queries and allows for
	modalities to report when a worklist item as been fulfilled.
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
from ..web.dicomweb import DicomResourceMixin, DicomUidJsonMixin

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

			# Add Tag Group Permissions
			headers[sonador_api.SONADOR_PERMISSIONS_HEADER] = json.dumps({
				'tag': _group_acl.tag or _user.is_superuser,
				'tag_modify': _group_acl.tag_modify or _user.is_superuser,
			}, cls=SonadorJsonEncoder)

			# Ensure that the headers are visible in the response
			# Ensure that the headers are visible in the response
			exposed_headers = headers.get(gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER) \
				or headers.get(gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER.lower()) \
				or headers.get(gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER.upper()) \
				or []

			exposed_headers.append(sonador_api.SONADOR_PERMISSIONS_HEADER)
			headers[gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER] = ', '.join(exposed_headers)

		return headers


class TagItemRestView(TagJsonMixin, GroupChildBaseRestView):
	'''	REST endpoint which can be used to retrieve details for, update, and delete series reviewer worklist items
	'''
	model = ImagingTag
	modelform = TagValidationForm

	def err_404(self, err, *args, **kwargs):
		'''	Create 404 error message which includes the UID of the group
		'''
		gid = self.get_group_uid(*args, **kwargs)
		uid = self.get_object_uid(*args, **kwargs)

		return 'Unable to retrieve Tag=%s for Group=%s. Tag instance does not exist.' \
			% (uid or '(none)', gid or '(none)')
