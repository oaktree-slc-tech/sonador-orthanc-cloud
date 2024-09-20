import logging, abc, datetime
from abc import ABC

from sonador.servers.auth import SonadorGroup

from ..db.cache import CachePatient, CacheStudy, CacheSeries

logger = logging.getLogger(__name__)


class ResourceAclMixin(abc.ABC):
	'''	Helper mixin which provides methods for filtering resource instances by user
		and group permissions.
	'''
	@abc.abstractmethod
	def _acl_parent_authorizations(self, acl_conditions, user, groups, **kwargs):
		'''	Add parent authorizations to the ACL conditions for the resource

			@returns acl_conditions
		'''

	def _group_pk(self, g):
		'''	Return the provided group UID
		'''
		if not isinstance(g, (SonadorGroup, dict)):
			raise TypeError('Unsupported group type')

		return g.pk if isinstance(g, SonadorGroup) else g.get(SonadorGroup.pk_attr)

	def apply_acl_queryfilter(self, dcm_resources, user, groups, **kwargs):
		'''	Restrict the list of resources to those that the user has an ACL policy
			(either user or group) granting access.
		'''
		# Start by filtering resources by user policies
		_acl = self.resource_model.auth_user.any(self.resource_model.user_acl_model.user == user.pk)

		# Filter resources by group policies
		for g in groups:

			# # Add group ACL condition to the query
			_acl |= self.resource_model.auth_group.any(self.resource_model.group_acl_model.group == self._group_pk(g))

		# Add permissions from parent resourcs
		_acl = self._acl_parent_authorizations(_acl, user, groups, **kwargs)

		# Apply query filter
		return dcm_resources.filter(_acl)


class PatientResourceAclMixin(ResourceAclMixin):
	'''	Helper mixin which provides methods for filtering patient resources by user and group permissions
	'''
	def _acl_parent_authorizations(self, acl_conditions, user, groups, **kwargs):
		'''	Query access to a patient record requires a direct grant, return acl_conditions unmodified.
		'''
		return acl_conditions


class StudyResourceAclMixin(ResourceAclMixin):
	'''	Helper mixin which provides methods for filtering patient resources by user and group permissions
	'''
	def _acl_parent_authorizations(self, acl_conditions, user, groups, **kwargs):
		'''	A patient policy grants access to child study instances. Add conditions which match the study's 
			parent for the user and groups.
		'''
		# Apply patient user policies
		acl_conditions |= self.resource_model.parent.has(
			CachePatient.auth_user.any(CachePatient.user_acl_model.user == user.pk))

		for g in groups:
			acl_conditions |= self.resource_model.parent.has(
				CachePatient.auth_group.any(CachePatient.group_acl_model.group == self._group_pk(g)))

		return acl_conditions


class SeriesResourceAclMixin(ResourceAclMixin):
	'''	Helper mixin which provides methods for filtering series resources by user and group permissions
	'''
	def _acl_parent_authorizations(self, acl_conditions, user, groups, **kwargs):
		'''	Both patient and study policies grant access to child series. Add conditions which match the study
			and parent for the user and groups.
		'''
		# Apply patient policies
		acl_conditions |= self.resource_model.parent.has(CacheStudy.parent.has(
			CachePatient.auth_user.any(CachePatient.user_acl_model.user == user.pk)))

		# Apply patient group policies
		for g in groups:
			acl_conditions |= self.resource_model.parent.has(CacheStudy.parent.has(
				CachePatient.auth_group.any(CachePatient.group_acl_model.group == self._group_pk(g))))

		# Apply study policies
		acl_conditions |= self.resource_model.parent.has(
			CacheStudy.auth_user.any(CacheStudy.user_acl_model.user == user.pk))

		# Apply study group policies
		for g in groups:
			acl_conditions |= self.resource_model.parent.has(
				CacheStudy.auth_group.any(CacheStudy.group_acl_model.group == self._group_pk(g)))

		return acl_conditions