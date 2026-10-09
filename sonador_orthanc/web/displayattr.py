'''	Web views for Display Attributes: DICOM attributes a group curates for the viewer's corner
	overlay. Per-group CRUD follows the Series Tags views; the aggregate view returns the union
	of every collection the requesting user may use.
'''
import json

from sqlalchemy.exc import IntegrityError

import client.apisettings as gcapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist

from sonador.serialization import SonadorJsonEncoder
from sonador import apisettings as sonador_api

from ..web.ext.group import GroupChildManagementBaseView, GroupChildBaseRestView
from ..web.secure_user import UserContextMixin, GroupLookupMixin

from ..db.displayattr import DisplayAttribute
from ..db.helpers import orthanc_displayattrjson

from ..validation.displayattr import DisplayAttributeValidationForm, duplicate_code_error

from .base import OrthancBaseView


def display_attr_permissions(acl, user):
	'''	Display Attributes permission flags (`display_attr` / `display_attr_modify`) for a user on one
		group policy, as the write rule on the form enforces them: nobody manages a collection whose
		policy does not enable display attributes; with it enabled, members need the modify flag and
		staff and superusers do not (Sonador's staff rule for this feature). `acl` may be None for a
		group without a policy.
	'''
	return DisplayAttributeValidationForm.policy_permissions(acl, user)


def expose_header(headers, name):
	'''	Add a header name to Access-Control-Expose-Headers
	'''
	exposed = headers.get(gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER) \
		or headers.get(gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER.lower()) \
		or headers.get(gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER.upper()) \
		or []

	if isinstance(exposed, str):
		exposed = [h.strip() for h in exposed.split(',') if h.strip()]

	exposed.append(name)
	headers[gcapicodes.ACCESS_CONTROL_EXPOSE_HEADERS_HEADER] = ', '.join(exposed)

	return headers


class DisplayAttributeJsonMixin:
	def orthanc_objectjson(self, attribute):
		return orthanc_displayattrjson(attribute, group=getattr(self, 'group', None))

	def err_404(self, err, *args, **kwargs):
		gid = self.get_group_uid(*args, **kwargs)
		uid = self.get_object_uid(*args, **kwargs) if hasattr(self, 'get_object_uid') else None

		if uid:
			return 'Unable to retrieve Display Attribute=%s for Group=%s. Attribute does not exist.' % (uid, gid or '(none)')

		return 'Unable to retrieve Display Attributes for Group=%s. Group does not exist or is not associated with the server.' \
			% (gid or '(none)')


class DisplayAttributePermissionHeaderMixin:
	'''	Adds the Display Attributes permission header to collection (GET) responses
	'''
	def get_response_headers(self, response, status_code, method, *args, **kwargs):
		headers = super().get_response_headers(response, status_code, method, *args, **kwargs)
		if method != gcapicodes.HTTP_GET:
			return headers

		if getattr(self, 'user', None) is None:
			self.init_user_context(self.request, *args, **kwargs)

		group = self.get_group(*args, **kwargs)
		acl = self.sonador_manager.get_internal_imageserver().fetch_acl().get_group_acl(group.pk)

		headers[sonador_api.SONADOR_PERMISSIONS_HEADER] = json.dumps(
			display_attr_permissions(acl, self.user), cls=SonadorJsonEncoder)

		return expose_header(headers, sonador_api.SONADOR_PERMISSIONS_HEADER)


class DisplayAttributeManagementView(DisplayAttributeJsonMixin, DisplayAttributePermissionHeaderMixin, UserContextMixin, GroupChildManagementBaseView):
	'''	List (GET) and create (POST) the display attributes of a group
	'''
	sessionmaker = None
	model = DisplayAttribute
	modelform = DisplayAttributeValidationForm

	def modelform_kwargs(self, *args, **kwargs):
		'''	Add the request user to the form so the policy write rule can be applied
		'''
		form_kwargs = super().modelform_kwargs(*args, **kwargs)
		if getattr(self, 'user', None) is None:
			self.init_user_context(self.request, *args, **kwargs)
		form_kwargs.update({ 'request_user': self.user, 'request_user_groups': getattr(self, 'groups', None) })
		return form_kwargs

	def save_object_data(self, session, form_instance, *args, **kwargs):
		'''	Persist the attribute. The form's duplicate check runs before the insert, so two
			concurrent creates of the same code can both pass it; the unique constraint on
			(group, code) then refuses the second insert, which is reported as the same
			duplicate validation error instead of a server error.
		'''
		try:
			return super().save_object_data(session, form_instance, *args, **kwargs)

		except IntegrityError:
			session.rollback()
			code = getattr(form_instance, 'Code', None)
			raise duplicate_code_error(code, code, self.get_group(*args, **kwargs))


class DisplayAttributeRestView(DisplayAttributeJsonMixin, UserContextMixin, GroupChildBaseRestView):
	'''	Retrieve (GET), relabel (PUT), and remove (DELETE) one display attribute of a group
	'''
	sessionmaker = None
	model = DisplayAttribute
	modelform = DisplayAttributeValidationForm

	def get_object(self, session, *args, uid=None, group=None, **kwargs):
		uid = uid or self.get_object_uid(*args, **kwargs)
		group = self.get_group(*args, **kwargs)

		obj = session.query(self.model).filter_by(group=int(group.pk), uid=uid).first()
		if not obj:
			raise ResourceDoesNotExist('Unable to retrieve object ID=%s for group=%s' % (uid, group.pk))

		return obj

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


class DisplayAttributeAggregateView(UserContextMixin, GroupLookupMixin, OrthancBaseView):
	'''	GET /display-attributes: the groups whose display attributes the requesting user may use,
		and the de-duplicated union of those collections.

		Members see the groups whose policy enables display attributes, manageable when it also
		carries the modify flag; staff and superusers see every group whose policy enables them, all
		manageable. When two groups define the same code, the entry of the group whose name sorts
		first is kept.
	'''
	sonador_manager = None
	sessionmaker = None

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		if not self.sonador_manager:
			raise ConfigurationError('Unable to initialize display attributes view, invalid Sonador manager instance.')
		if self.sessionmaker is None:
			raise ConfigurationError('Unable to initialize display attributes view, invalid session maker instance.')

	def scoped_groups(self):
		'''	`[{ 'id', 'name', 'manage' }]` sorted by name, for the request user
		'''
		acl = self.sonador_manager.get_internal_imageserver().fetch_acl()
		user = self.user

		# Staff and superusers: every group whose policy enables display attributes, all manageable.
		# A disabled policy is out of scope for them too, since no write on it is allowed.
		if getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
			pks = sorted(set(int(policy.group) for policy in acl if getattr(policy, 'display_attr', False)))
			groups = [{ 'id': g.pk, 'name': g.name, 'manage': True } for g in (self.sonador_group_lookup(pks) if pks else [])]

		else:
			groups = []
			for membership in (user._objectdata.get('groups') or []):
				gid = membership.get('id')
				perms = display_attr_permissions(acl.get_group_acl(gid), user)
				if perms['display_attr']:
					groups.append({ 'id': gid, 'name': membership.get('name'), 'manage': perms['display_attr_modify'] })

		return sorted(groups, key=lambda g: ((g['name'] or '').lower(), g['id']))

	def get(self, output, uri, request, *args, **kwargs):
		self.init_user_context(request, *args, **kwargs)

		groups = self.scoped_groups()
		rank = dict((g['id'], index) for index, g in enumerate(groups))
		names = dict((g['id'], g) for g in groups)

		tags = []
		if groups:
			with self.sessionmaker() as session:
				rows = session.query(DisplayAttribute).filter(DisplayAttribute.group.in_(list(rank.keys()))).all()

			seen = set()
			for row in sorted(rows, key=lambda r: (rank.get(int(r.group), len(rank)), r.code)):
				if int(row.group) not in rank or row.code in seen:
					continue

				seen.add(row.code)
				tags.append(orthanc_displayattrjson(row, group=names.get(int(row.group))))

		return self.send_response(json.dumps({ 'groups': groups, 'tags': tags }, cls=SonadorJsonEncoder), status_code=200)
