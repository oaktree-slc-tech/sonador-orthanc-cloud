"""	ACL policy serialization and principal lookup: a policy always names its principal, at least by
	id, and one id Sonador cannot resolve does not leave the others unresolved.
"""
import types

import pytest

pytest.importorskip('sqlalchemy')

from client.errors import ClientOperationError

from sonador_orthanc.db.auth import GroupSeriesAuth, UserSeriesAuth
from sonador_orthanc.db.helpers import orthanc_auth_resourcejson
from sonador_orthanc.web.secure_user import GroupLookupBaseMixin, UserLookupBaseMixin


SERIES_ID = '437ce199-d7e4b001-5e779d3f-2077c411-5beab5cd'


class FakeCollection:
	def __init__(self, models):
		self._models = { m.pk: m for m in models }

	def get_modelinstance(self, pk):
		return self._models.get(pk)

	def extend(self, other):
		self._models.update(other._models)


def group(pk, name):
	return types.SimpleNamespace(pk=pk, id=pk, name=name)


# Serialization

def test_group_policy_names_its_group_by_id_when_the_lookup_gave_nothing():
	auth = GroupSeriesAuth(uid='p1', resource=SERIES_ID, group=14, view=True)

	data = orthanc_auth_resourcejson(auth)

	assert data['Group'] == { 'id': 14 }
	assert data['ID'] == 'p1' and data['Series'] == SERIES_ID and data['View'] is True


def test_group_policy_carries_the_resolved_group_details():
	auth = GroupSeriesAuth(uid='p1', resource=SERIES_ID, group=26)

	data = orthanc_auth_resourcejson(auth, group=group(26, 'consult'))

	assert data['Group'] == { 'id': 26, 'name': 'consult' }


def test_user_policy_names_its_user_by_id_when_the_lookup_gave_nothing():
	auth = UserSeriesAuth(uid='p2', resource=SERIES_ID, user=13)

	assert orthanc_auth_resourcejson(auth)['User'] == { 'id': 13 }


# Lookup fallback

class Lookup(GroupLookupBaseMixin):
	"""	Sonador answers the bulk request with a 400 because id 14 is unknown; single lookups work
		for the ids it knows.
	"""
	known = { 26: group(26, 'consult'), 31: group(31, 'radiology') }

	def __init__(self):
		self.sonador_manager = types.SimpleNamespace(get_internal_imageserver=lambda: object())
		self.calls = []

	def _execute_group_lookup(self, iserver, group_uids):
		self.calls.append(list(group_uids))
		unknown = [uid for uid in group_uids if uid not in self.known]

		if unknown:
			raise ClientOperationError('Unable to execute group lookup due to an error.', http_code=400,
				details={ 'status-code': 400 })

		return FakeCollection([self.known[uid] for uid in group_uids])


def test_bulk_lookup_is_used_when_every_id_resolves():
	lookup = Lookup()

	collection = lookup.sonador_group_lookup([26, 31])

	assert lookup.calls == [[26, 31]]
	assert collection.get_modelinstance(26).name == 'consult'


def test_one_unknown_id_does_not_blank_the_others(caplog):
	lookup = Lookup()

	collection = lookup.sonador_group_lookup([26, 14, 31])

	assert collection.get_modelinstance(26).name == 'consult'
	assert collection.get_modelinstance(31).name == 'radiology'
	assert collection.get_modelinstance(14) is None
	assert len(lookup.calls) == 4   # the refused bulk call, then one per id
	assert '[14]' in caplog.text


def test_nothing_resolvable_yields_none():
	assert Lookup().sonador_group_lookup([14]) is None


def test_a_failure_that_is_not_about_the_ids_is_raised():
	class Down(GroupLookupBaseMixin):
		def __init__(self):
			self.sonador_manager = types.SimpleNamespace(get_internal_imageserver=lambda: object())

		def _execute_group_lookup(self, iserver, group_uids):
			raise ClientOperationError('Sonador unreachable', http_code=502, details={ 'status-code': 502 })

	with pytest.raises(ClientOperationError):
		Down().sonador_group_lookup([26])


# Listing

def test_listing_omits_policies_whose_principal_sonador_no_longer_knows(caplog):
	from sonador_orthanc.auth.web import AuthManagementView
	from sonador_orthanc.db.cache import CacheSeries

	known = { 26: group(26, 'consult') }

	class Query:
		def __init__(self, rows): self.rows = rows
		def filter_by(self, **kwargs): return iter(self.rows)

	policies = [
		GroupSeriesAuth(uid='keep', resource=SERIES_ID, group=26, view=True),
		GroupSeriesAuth(uid='stale', resource=SERIES_ID, group=14, view=True),
	]
	session = types.SimpleNamespace(query=lambda model: Query(policies))

	view = AuthManagementView(model=GroupSeriesAuth, resource_cachemodel=CacheSeries,
		sonador_manager=types.SimpleNamespace(get_internal_imageserver=lambda: object()), sessionmaker=lambda: session)

	def execute(iserver, group_uids):
		if any(uid not in known for uid in group_uids):
			raise ClientOperationError('unknown', http_code=400, details={ 'status-code': 400 })
		return FakeCollection([known[uid] for uid in group_uids])

	view._execute_group_lookup = execute

	listed = view.get_objects(session, ruid=SERIES_ID)

	assert [p.uid for p in listed] == ['keep']
	assert view.orthanc_objectjson(listed[0])['Group'] == { 'id': 26, 'name': 'consult' }
	assert 'uid=stale' in caplog.text and 'id=14' in caplog.text


def test_user_lookup_uses_the_same_fallback():
	class UserLookup(UserLookupBaseMixin):
		def __init__(self):
			self.sonador_manager = types.SimpleNamespace(get_internal_imageserver=lambda: object())

		def _execute_user_lookup(self, iserver, user_uids):
			if len(user_uids) > 1:
				raise ClientOperationError('bulk refused', http_code=400)
			if user_uids == [99]:
				raise ClientOperationError('unknown', http_code=400)
			return FakeCollection([types.SimpleNamespace(pk=uid, username='u%s' % uid) for uid in user_uids])

	collection = UserLookup().sonador_user_lookup([13, 99])

	assert collection.get_modelinstance(13).username == 'u13'
	assert collection.get_modelinstance(99) is None


def test_dicomweb_listing_omits_the_same_policies(caplog):
	'''	The DICOMweb route (the one the viewer's Share Access dialog reads) addresses the resource by
		its DICOM UID and must apply the same rule as the Orthanc-id route.
	'''
	from sonador_orthanc.auth.web import AuthDICOMManagementView
	from sonador_orthanc.db.cache import CacheSeries

	known = { 26: group(26, 'consult') }

	class Query:
		def __init__(self, rows): self.rows = rows
		def filter_by(self, **kwargs):
			assert kwargs == { 'resource': SERIES_ID }
			return iter(self.rows)

	policies = [
		GroupSeriesAuth(uid='keep', resource=SERIES_ID, group=26, view=True),
		GroupSeriesAuth(uid='stale', resource=SERIES_ID, group=14, view=True),
	]
	session = types.SimpleNamespace(query=lambda model: Query(policies))

	view = AuthDICOMManagementView(model=GroupSeriesAuth, resource_cachemodel=CacheSeries,
		sonador_manager=types.SimpleNamespace(get_internal_imageserver=lambda: object()), sessionmaker=lambda: session,
		dicom_uid_header='SeriesInstanceUID')

	# The DICOM UID in the URL resolves to the cached resource, whose Orthanc id keys the policies
	view.get_resource = lambda session, *args, **kwargs: types.SimpleNamespace(publicid=SERIES_ID)

	def execute(iserver, group_uids):
		if any(uid not in known for uid in group_uids):
			raise ClientOperationError('unknown', http_code=400, details={ 'status-code': 400 })
		return FakeCollection([known[uid] for uid in group_uids])

	view._execute_group_lookup = execute

	listed = view.get_objects(session)

	assert [p.uid for p in listed] == ['keep']
	assert view.acltype_collection.get_modelinstance(26) is known[26]
	assert 'uid=stale' in caplog.text and 'id=14' in caplog.text
