''' Web views which provide an API compatible with the Unified Procedure Step DICOM specification.
	UPS facilitates the retrieval of scheduled procedures via worklist queries and allows for
	modalities to report when a worklist item as been fulfilled.
'''
import json, uuid

from client.errors import ConfigurationError
from client.utils.object import pick, omit
from client.errors import ConfigurationError, ResourceDoesNotExist, ClientOperationError

from sonador.serialization import SonadorJsonEncoder
from sonador.apisettings import DCMHEADER_SERIES_INSTANCE_UID

from ..web.ext import ObjectManagementView, ObjectRestView
from ..web.helpers import paginate_query_results
from ..web.secure_user import UserLookupMixin, GroupLookupMixin
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
		# Match worklist with group data
		if getattr(self, 'group_collection', None):
			group = self.group_collection.get_modelinstance(w.group)
		else: group = None

		return orthanc_tagjson(t, group=group)


class TagItemManagementView(TagJsonMixin, GroupLookupMixin, ObjectManagementView):
	'''	Management endpoint which can be used to work with tag items
	'''
	sessionmaker = None
	model = ImagingTag
	modelform = TagValidationForm

	def get_objects_kwargs(self, *args, **kwargs):
		'''	Retrieve keyword arguments for "get_objects".
		'''
		kwargs = super().get_objects_kwargs(*args, **kwargs)

		# Retrieve group ID
		gid = kwargs.get('gid') or self.get_group_uid(*args, **kwargs)

		# Retrieve group instance
		if getattr(self, 'group', None):

			# Utilize cached copy of group
			group = self.group
		
		else:

			# Fetch group instance from Sonador
			_groups = self.sonador_group_lookup([gid])
			if not _groups:
				raise ResourceDoesNotExist('Unable to retrieve tags for group. Group does not exist or is not '
					+ 'associated with the imaging server.')

			setattr(self, 'group', _groups[0])
			group = self.group

		return { 'gid': gid, 'group': group }
 
	def get_objects(self, session, *args, **kwargs):
		'''	Retrieve the tag instances 
		'''
		gid = kwargs.get('gid')		
		if not gid:
			raise ValueError('Invalid group ID: %s' % gid)

		# Retrieve tags for active group
		return session.query(self.model).filter_by(group=int(gid))

	def init_object_kwargs(self, *args, **kwargs):
		return self.get_objects_kwargs(*args, **kwargs)

	def init_object_model(self, gid=None, **kwargs):
		'''	Initialize new grant model instance
		'''	
		gid = gid or self.get_group_uid(*args, **kwargs)
		return self.model(uid=str(uuid.uuid4()), group=int(gid))


class TagItemRestView(TagJsonMixin, GroupLookupMixin, ObjectRestView):
	'''	REST endpoint which can be used to retrieve details for, update, and delete series reviewer worklist items
	'''
	model = ImagingTag
	modelform = TagValidationForm

	def get_object_kwargs(self, *args, **kwargs):
		'''	Add keyword arguments to the get_object method of the view
		'''
		fetch_kwargs = super().get_object_kwargs(*args, **kwargs)
		fetch_kwargs['gid'] = gid = self.get_group_uid(*args, **kwargs)

		# Retrieve group instance
		if getattr(self, 'group', None):
			fetch_kwargs['group'] = self.group

		else:

			# Fetch group instance from Sonador
			_groups = self.sonador_group_lookup([gid])
			if not _groups:
				raise ResourceDoesNotExist('Unable to retrieve tags for group. Group does not exist or is not associated '
					'with the imaging server.')

			setattr(self, 'group', _groups[0])
			fetch_kwargs['group'] = self.group

		return fetch_kwargs

	def get_object(self, session, *args, uid=None, gid=None, **kwargs):
		'''	Retrieve a worklist for the provided ID. Throws ResourceDoesNotExist
			if unable to find either the parent series or a worklist with the provided UID.

			@returns worklist instance
		'''
		# Retrieve group ID and tag ID from URL (or from options)
		uid = uid or self.get_object_uid(*args, **kwargs)
		gid = gid or self.get_group_uid(*args, **kwargs)

		# Retrieve tag from database
		tag = session.query(self.model).filter_by(group=gid, uid=uid).first()
		if not tag:
			raise ResourceDoesNotExist('Unable to retrieve tag ID=%s' % (uid))

		return tag

	def err_404(self, err, *args, **kwargs):
		'''	Create 404 error message which includes the UID of the group
		'''
		gid = self.get_group_uid(*args, **kwargs)
		uid = self.get_object_uid(*args, **kwargs)

		return 'Unable to retrieve Tag=%s for Group=%s. Tag instance does not exist.' % (
			uid or '(none)', gid or '(none)',
		)

	def update_response_json(self, obj, *args, **kwargs):
		'''	Create the JSON response for an update request, add group ID
		'''
		# Create response data structure, add group ID
		rdata = super().update_response_json(obj, *args, **kwargs)
		rdata['Group'] = self.get_group_uid(*args, **kwargs)

		return rdata

	def delete_resposne_json(self, obj, *args, **kwargs):
		'''	Create the JSON response structure for a delete request
		'''
		# Create response data structure, add group ID
		rdata = super().update_response_json(obj, *args, **kwargs)
		rdata['Group'] = self.get_group_uid(*args, **kwargs)

		return rdata