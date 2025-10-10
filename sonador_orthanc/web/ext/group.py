'''	Web views to help with the management of extension models which are associated with a parent group.
	View instances inherit from ext.base.ObjectManagementBaseView and ext.base.ObjectBaseRestView.

	*	GroupChildManagementBaseView: view class which can be used to create
		new chidl object instances and to retrieve a list of child objects associated
		with a specific group.
		- POST: create a new child instance
		- GET: retrieve a list of child instances with a specific parent
	* 	GroupChildBaseRestView: view class which can be used to work with a specific instance of a
		of a child object.
		- GET: retrieve details for the child instance
		- PUT: update attributes of the child
		- DELETE: remove the child instance
'''
import abc, logging, posixpath, pydicom, json, copy, datetime, traceback, uuid

from pydantic import ValidationError as PydanticValidationError

import client.apisettings as gcapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist
from client.utils.object import pick, omit

from ..secure_user import GroupLookupMixin

from .object import ObjectManagementView, ObjectRestView


class GroupChildViewMixin(GroupLookupMixin):
	'''	Mixin class which provides methods and properties for common REST actions for group views.
	'''
	def get_group(self, *args, gid=None, **kwargs):
		'''	Retrieve the group associated with the view (aches a copy of the group on the 
			view instance). Throws a 404 error if the group does not exist or is not assocaited
			with the imaging server.
		'''
		gid = gid or self.get_group_uid(*args, **kwargs)

		# Retrieve group instance
		if getattr(self, 'group', None):
			return self.group

		else:

			# Fetch group instance from Sonador
			_groups = self.sonador_group_lookup([gid])
			if not _groups:
				raise ResourceDoesNotExist(('Unable to retrieve data. Group "%s" does not exist or '
					+ 'is not associated with the imaging server.') % gid)

			setattr(self, 'group', _groups[0])
			return self.group


class GroupChildManagementBaseView(GroupChildViewMixin, ObjectManagementView):
	'''	View instance which can be used to create and retrieve collections of group child objects.
	'''
	group_foreign_key_attr = 'group'

	def get_objects_kwargs(self, *args, **kwargs):
		'''	Retreive keyword arguments for get_objects.
		'''
		kwargs = super().get_objects_kwargs(*args, **kwargs)

		group = self.get_group(*args, **kwargs)
		kwargs['gid'] = group.pk
		kwargs[self.group_foreign_key_attr] = group

		return kwargs

	def get_objects(self, session, *args, group=None, **kwargs):
		'''	Retrieve objects associated with the group
		'''
		group = group or self.get_group(*args, **kwargs)
		return session.query(self.model).filter_by(**{
			self.group_foreign_key_attr: group.pk
		})

	def init_object_kwargs(self, *args, **kwargs):
		'''	Add keyword arguments to the init_object_model method of the view
		'''
		init_kwargs = super().init_object_kwargs(*args, **kwargs)
		init_kwargs.update({
			**pick(kwargs, ('session',)),
			**self.get_objects_kwargs(*args, **kwargs),
		})

		return init_kwargs

	def modelform_kwargs(self, *args, **kwargs):
		'''	Add keyword arguments for modelform "clean" method including the group,
			session, sonador_manager, and model.
		'''
		form_kwargs = super().modelform_kwargs(*args, **kwargs)
		form_kwargs['group'] = self.get_group(*args, **kwargs)

		form_kwargs.update({
			**pick(kwargs, ('session', 'create')),
			**pick(self, ('sonador_manager', 'model')),
		})

		return form_kwargs

	def init_object_model(self, *args, **kwargs):
		'''	Initialize a new instance of the model
		'''
		# Initialize a new model instance with a UID and the group instance associated
		# with the view.
		instance = self.model(uid=str(uuid.uuid4()))
		setattr(instance, self.group_foreign_key_attr, int(self.get_group(*args, **kwargs).pk))

		return instance


class GroupChildBaseRestView(GroupChildViewMixin, ObjectRestView):
	'''	View instance which can be used to retieve details (GET), update (PUT), or
		remove (DELETE) a group child object.
	'''
	def get_object(self, session, *args, uid=None, group=None, **kwargs):
		'''	Retrieve the child object specified by the UID. Throws ResourceDoesNotExist
			if unable to retrieve the object or the group.
		'''
		# Retrieve device and group UIDs
		uid = uid or self.get_object_uid(*args, **kwargs)
		group = self.get_group(*args, **kwargs)

		obj = session.query(self.model).filter_by(group=int(group.pk), uid=uid).first()
		if not obj:
			return ResourceDoesNotExist('Unable to retrieve object ID=%s for group=%s' % (uid, group.pk))

		return obj

	def modelform_kwargs(self, **kwargs):
		'''	Add keyword arguments for modelform's "clean" method
		'''
		form_kwargs = super().modelform_kwargs(**kwargs)

		# Check submitted form request and back-fill attributes missing from request
		# with those of the existing model instance
		_obj = kwargs.get('obj')
		if _obj:
			form_kwargs = self._backfill_object_attrs(_obj, attrs=form_kwargs)
		
		# Sonador manager, session, and model instance
		form_kwargs.update({
			**pick(kwargs, ('session', 'update', 'obj')),
			**pick(self, ('sonador_manager', 'model')),
			'group': self.get_group(**kwargs),
		})

		return form_kwargs

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