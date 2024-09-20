'''	Mixins and view instances which allow for requests to secured/scoped by user or group permissions.
'''
import logging, traceback, abc

from client.errors import ConfigurationError, ResourceDoesNotExist, ClientOperationError

from sonador.apisettings.base import IMAGING_SESRVER_GROUP_UID_REGEX
from sonador.servers.auth import SonadorUser, SonadorGroupCollection, ACL_PERM_QUERY
from sonador.errors import soandor_clientexception_server_errors

logger = logging.getLogger(__name__)


class UserContextMixin:
	'''	Mixin class which provides methods to retrieve a Sonador user context using
		the Sonador authentication token.

		@property user (SonadorUser): user instance associated with the request. Retrieved
			from Sonador by identifying the authorization credential.
		@property groups (SonadorGroupCollection): groups to which the user belongs.
	'''
	def init_user_context(self, request, *args, **kwargs):
		'''	Retrieve the user context for the view instance
		'''
		if not getattr(self, 'sonador_manager', None):
			raise ConfigurationError('Unable to retrieve user context, invalid Sonador manager instance')

		# Retrieve image server reference
		_iserver = self.sonador_manager.get_internal_imageserver()

		# Retrieve user context from Sonador to scope request
		self.user = kwargs.get('user') or request.get('user')
		if self.user is None:

			# Retrieve user credential from authorization header: should have two components.
			# * Example 1: "api-token <token-value>"
			# * Example 2: "Bearer <token-value>"
			creds = request.get('headers', {}).get('authorization', '').split(' ')
			if len(creds) != 2:
				raise ValueError('Unable to retrieve user context, invalid credentials "%s"' % creds)

			_context = _iserver.admin_verify_user_credentials(creds[0], creds[1])
			self.user = SonadorUser(_iserver, _context.get('user', {}))
		
		# Retrieve user groups
		self.groups = SonadorGroupCollection(_iserver, self.user._objectdata.get('groups', []))



class UserLookupBaseMixin(abc.ABC):
	'''	Mixin class whcih providces methods to lookup user instances
	'''
	user_lookup_error_template = 'Unable to retrieve user data for user-ids="%s". Error: "%s".\nServer Response: "%s"\n%s'

	@abc.abstractmethod
	def _execute_user_lookup(self, user_uids):
		'''	Execute lookup for the provided UIDs
		'''

	def sonador_user_lookup(self, user_uids):
		'''	Lookup user data for the provided user UIDs
		'''
		_iserver = self.sonador_manager.get_internal_imageserver()
		user_collection = None

		# Attempt to retrieve user data for the specified IDs
		try: user_collection = self._execute_user_lookup(_iserver, user_uids)
		except ClientOperationError as err:
			server_errors = soandor_clientexception_server_errors(err)

			logger.error(self.user_lookup_error_template % (
				user_uids, err, server_errors, traceback.format_exc(),
			))

		return user_collection


class UserLookupMixin(UserLookupBaseMixin):
	'''	Mixin class which provides methods to lookup user instances: lookup queries only retrieve
		users which are associated with the imaging server instance.
	'''
	def _execute_user_lookup(self, iserver, user_uids):
		return iserver.user_lookup(list(user_uids))


class AdminUserLookupMixin(UserLookupBaseMixin):
	'''	Mixin class which provides methods to lookup user instances: lookup queries retrieve
		any user registered on the Sonador platform.
	'''
	def _execute_user_lookup(self, iserver, user_uids):
		return iserver.server.admin_user_lookup(list(user_uids))


class GroupLookupBaseMixin(abc.ABC):
	'''	Mixin class whcih providces methods to lookup user instances
	'''
	group_lookup_error_template = 'Unable to retrieve group data for group-ids="%s". Error: "%s".\nServer Response: "%s"\n%s'

	@abc.abstractmethod
	def _execute_group_lookup(self, iserver, group_uids):
		'''	Execute lookup for the provided UIDs
		'''

	def sonador_group_lookup(self, group_uids):
		'''	Lookup group data for the provided UIDs
		'''
		_iserver = self.sonador_manager.get_internal_imageserver()
		group_collection = None

		# Attempt to retrieve group data for the specified IDs
		try: group_collection = self._execute_group_lookup(_iserver, list(group_uids))
		except ClientOperationError as err:
			server_errors = soandor_clientexception_server_errors(err)

			logger.error(self.group_lookup_error_template % (
				group_uids, err, server_errors, traceback.format_exc()
			))

		return group_collection


class GroupLookupMixin(GroupLookupBaseMixin):
	'''	Mixin class which provides methods to lookup group instances: lookup queries only retrieve
		groups which are associated with the imaging server instance.
	'''
	group_uid_regex = IMAGING_SESRVER_GROUP_UID_REGEX

	def get_group_uid(self, *args, resource_uri=None, **kwargs):
		'''	Retrieve group UID FROM THE url

			@returns str or None: UID if there was a match, None otherwise
		'''
		resource_uri = resource_uri or self.uri
		guid_match = self.group_uid_regex.match(resource_uri)
		return guid_match.groupdict().get('group') if guid_match else None

	def _execute_group_lookup(self, iserver, group_uids):
		return iserver.group_lookup(list(group_uids))


class AdminGroupLookupMixin(GroupLookupBaseMixin):
	'''	Mixin class which provides methods to lookup group instances: lookup queries retrieve
		any group defined within the Sonador platform.
	'''
	def _execute_group_lookup(self, iserver, group_uids):
		return iserver.server.admin_group_lookup(list(group_uids))