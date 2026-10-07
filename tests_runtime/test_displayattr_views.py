'''	Display Attributes: the validation form (code normalisation, catalogue membership, duplicates, fixed
	code on update), the collection permission header, and the aggregate view's scoping and
	de-duplication. The database session, the Sonador ACL and the user profile are doubles.
'''
import json, types

import pytest

pytest.importorskip('sqlalchemy')
pytest.importorskip('pydantic')

from pydantic import ValidationError as PydanticValidationError

from sonador_orthanc.db.displayattr import DisplayAttribute
from sonador_orthanc.db.helpers import normalize_dcm_code, orthanc_displayattrjson
from sonador_orthanc.validation import displayattr as displayattr_validation
from sonador_orthanc.validation.displayattr import DisplayAttributeValidationForm
from sonador_orthanc.web.displayattr import DisplayAttributeAggregateView, DisplayAttributeManagementView, display_attr_permissions
from sonador_orthanc.web.system import catalogue_tagdef


CATALOGUE = {
	'Study': { '0008,1030': { 'code': '0008,1030', 'tag': 'StudyDescription', 'label': 'Study Description', 'private': False, 'vr': { 'code': 'LO' } } },
	'Series': { '0018,1030': { 'code': '0018,1030', 'tag': 'ProtocolName', 'label': 'Protocol Name', 'private': False, 'vr': { 'code': 'LO' } } },
	'Instance': { '0043,102D': { 'code': '0043,102D', 'tag': 'GEDistortionFilter', 'label': 'Distortion Filter', 'private': True, 'vr': { 'code': 'SH' } } },
}


class Query:
	def __init__(self, rows):
		self.rows = rows
		self.criteria = {}

	def filter_by(self, **criteria):
		self.criteria = criteria
		return self

	def filter(self, *args):
		return self

	def first(self):
		for row in self.rows:
			if all(getattr(row, key) == value for key, value in self.criteria.items()):
				return row
		return None

	def all(self):
		return list(self.rows)


class Session:
	def __init__(self, rows=()):
		self.rows = list(rows)

	def query(self, model):
		return Query(self.rows)

	def add(self, obj):
		self.rows.append(obj)

	def commit(self):
		pass

	def delete(self, obj):
		self.rows.remove(obj)

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False


class Output:
	def __init__(self):
		self.status, self.body, self.headers = None, None, {}

	def SetHttpHeader(self, key, value):
		self.headers[key] = value

	def SendHttpStatus(self, status, body):
		self.status, self.body = status, body

	def AnswerBuffer(self, body, mtype):
		self.status, self.body = 200, body


class Acl:
	def __init__(self, policies):
		self.policies = [types.SimpleNamespace(**p) for p in policies]

	def __iter__(self):
		return iter(self.policies)

	def get_group_acl(self, pk):
		for policy in self.policies:
			if policy.group == pk:
				return policy
		return None


def user(groups=(), **flags):
	data = { 'id': 7, 'username': 'tester', 'is_staff': False, 'is_superuser': False,
		'groups': [{ 'id': gid, 'name': name } for gid, name in groups], **flags }
	return types.SimpleNamespace(_objectdata=data, pk=7, **data)


def group(pk, name):
	return types.SimpleNamespace(pk=pk, id=pk, name=name)


def manager(policies):
	iserver = types.SimpleNamespace(fetch_acl=lambda: Acl(policies), system_info=lambda: {})
	return types.SimpleNamespace(get_internal_imageserver=lambda: iserver, tags=None)


def row(uid, gid, code, keyword='X', label=None):
	return DisplayAttribute(uid=uid, group=gid, code=code, keyword=keyword, label=label, private=False)


@pytest.fixture(autouse=True)
def catalogue(monkeypatch):
	monkeypatch.setattr(displayattr_validation, 'build_dcmtag_catalogue', lambda manager: CATALOGUE)


# Helpers

@pytest.mark.parametrize('value, expected', [
	('0018,1030', '0018,1030'),
	('(0018,1030)', '0018,1030'),
	('00181030', '0018,1030'),
	('x0043102d', '0043,102D'),
	(' 0043, 102d ', '0043,102D'),
])
def test_normalize_dcm_code(value, expected):
	assert normalize_dcm_code(value) == expected


@pytest.mark.parametrize('value', ['0018103', 'ProtocolName', '', None, 12345678])
def test_normalize_dcm_code_rejects(value):
	with pytest.raises(ValueError):
		normalize_dcm_code(value)


def test_catalogue_tagdef_matches_any_level():
	assert catalogue_tagdef(CATALOGUE, '0043,102D')['private'] is True
	assert catalogue_tagdef(CATALOGUE, '0018,1030')['tag'] == 'ProtocolName'
	assert catalogue_tagdef(CATALOGUE, '0008,0080') is None
	assert catalogue_tagdef({}, '0018,1030') is None


def test_displayattrjson_shape():
	tag = row('u1', 5, '0018,1030', keyword='ProtocolName', label='Protocol')
	data = orthanc_displayattrjson(tag, group=group(5, 'readers'))

	assert data == { 'ID': 'u1', 'Created': None, 'Modified': None, 'Code': '0018,1030', 'Tag': 'ProtocolName',
		'Label': 'Protocol', 'Private': False, 'Group': { 'id': 5, 'name': 'readers' } }
	assert 'Group' not in orthanc_displayattrjson(tag)


# Validation form

def form_kwargs(session=None, obj=None, **extra):
	return { 'sonador_manager': manager([]), 'session': session if session is not None else Session(), 'model': DisplayAttribute,
		'group': group(5, 'readers'), 'create': obj is None, 'update': obj is not None, 'obj': obj, **extra }


def test_form_normalises_and_fills_from_catalogue():
	form = DisplayAttributeValidationForm.clean(Code='(0018,1030)', Label='  Protocol ', **form_kwargs())

	assert form.Code == '0018,1030'
	assert form.Label == 'Protocol'
	assert form.Tag == 'ProtocolName'
	assert form.Private is False

	private = DisplayAttributeValidationForm.clean(Code='0043102d', **form_kwargs())
	assert private.Code == '0043,102D' and private.Private is True and private.Label is None


def test_form_rejects_a_code_outside_the_catalogue():
	with pytest.raises(PydanticValidationError) as err:
		DisplayAttributeValidationForm.clean(Code='0008,0080', **form_kwargs())

	assert 'not indexed' in str(err.value)


def test_form_rejects_a_malformed_code():
	with pytest.raises(PydanticValidationError) as err:
		DisplayAttributeValidationForm.clean(Code='ProtocolName', **form_kwargs())

	assert 'GGGG,EEEE' in str(err.value)


def test_form_rejects_a_duplicate_within_the_group():
	session = Session([row('u1', 5, '0018,1030')])

	with pytest.raises(PydanticValidationError) as err:
		DisplayAttributeValidationForm.clean(Code='00181030', **form_kwargs(session=session))

	assert 'already part of the collection' in str(err.value)

	# The same code in another group is not a duplicate
	DisplayAttributeValidationForm.clean(Code='00181030', **form_kwargs(session=session, group=group(6, 'curators')))


def test_form_keeps_the_code_of_an_existing_entry():
	existing = row('u1', 5, '0018,1030')
	session = Session([existing])

	form = DisplayAttributeValidationForm.clean(Code='0018,1030', Label='Renamed', **form_kwargs(session=session, obj=existing))
	assert form.Label == 'Renamed'

	with pytest.raises(PydanticValidationError) as err:
		DisplayAttributeValidationForm.clean(Code='0008,1030', **form_kwargs(session=session, obj=existing))

	assert 'cannot be changed' in str(err.value)


def test_form_saves_through_the_fieldmap():
	session = Session()
	target = DisplayAttribute(uid='new', group=5)
	DisplayAttributeValidationForm.clean(Code='0043,102D', Label='Filter', **form_kwargs(session=session)).save(session, target)

	assert (target.code, target.keyword, target.label, target.private) == ('0043,102D', 'GEDistortionFilter', 'Filter', True)


# Permission header

@pytest.mark.parametrize('policy, flags, expected', [
	({ 'group': 5, 'display_attr': True, 'display_attr_modify': False }, {}, (True, False)),
	({ 'group': 5, 'display_attr': True, 'display_attr_modify': True }, {}, (True, True)),
	({ 'group': 5 }, {}, (False, False)),
	(None, {}, (False, False)),
	(None, { 'is_staff': True }, (False, False)),
	({ 'group': 5 }, { 'is_staff': True }, (False, False)),
	({ 'group': 5, 'display_attr': True }, { 'is_staff': True }, (True, True)),
	(None, { 'is_superuser': True }, (True, True)),
])
def test_display_attr_permissions(policy, flags, expected):
	acl = types.SimpleNamespace(**policy) if policy else None
	perms = display_attr_permissions(acl, user(**flags))

	assert (perms['display_attr'], perms['display_attr_modify']) == expected


def test_collection_header_for_a_group_without_a_policy():
	view = DisplayAttributeManagementView(sonador_manager=manager([]), sessionmaker=lambda: Session())
	view.uri = '/groups/5/display-attributes'
	view.request = { 'method': 'GET', 'headers': {}, 'get': {} }
	view.user = user(groups=[(5, 'readers')])
	view.group = group(5, 'readers')

	headers = view.get_response_headers([], 201, 'GET')

	assert json.loads(headers['sonador-permissions']) == { 'display_attr': False, 'display_attr_modify': False }
	assert 'sonador-permissions' in headers['Access-Control-Expose-Headers']


# Aggregate view

def aggregate(policies, rows, requester, lookup=None):
	view = DisplayAttributeAggregateView(sonador_manager=manager(policies), sessionmaker=lambda: Session(rows))
	view.uri = '/display-attributes'
	view.request = { 'method': 'GET', 'headers': {}, 'get': {} }
	view.output = Output()
	view.init_user_context = lambda request, *args, **kwargs: setattr(view, 'user', requester)
	view.sonador_group_lookup = lambda pks: [group(pk, (lookup or {}).get(pk, 'group-%d' % pk)) for pk in pks]
	view.get(view.output, view.uri, view.request)

	return view.output.status, json.loads(view.output.body)


POLICIES = [
	{ 'group': 5, 'display_attr': True, 'display_attr_modify': False },
	{ 'group': 6, 'display_attr': True, 'display_attr_modify': True },
	{ 'group': 7 },
]
ROWS = [
	row('a', 5, '0018,1030', label='Readers protocol'),
	row('b', 6, '0018,1030', label='Curators protocol'),
	row('c', 6, '0008,1030'),
	row('d', 7, '0043,102D'),
]


def test_member_sees_only_groups_that_grant_read():
	status, data = aggregate(POLICIES, ROWS, user(groups=[(5, 'readers'), (6, 'curators'), (7, 'others')]))

	assert status == 200
	assert data['groups'] == [
		{ 'id': 6, 'name': 'curators', 'manage': True },
		{ 'id': 5, 'name': 'readers', 'manage': False },
	]
	assert [(t['Code'], t['Group']['name']) for t in data['tags']] == [
		('0008,1030', 'curators'), ('0018,1030', 'curators'),
	]


def test_duplicate_code_resolves_to_the_group_whose_name_sorts_first():
	status, data = aggregate(POLICIES, ROWS, user(groups=[(5, 'alpha'), (6, 'beta')]))

	assert [t['Label'] for t in data['tags'] if t['Code'] == '0018,1030'] == ['Readers protocol']


def test_member_without_any_grant_gets_empty_lists():
	status, data = aggregate(POLICIES, ROWS, user(groups=[(7, 'others')]))

	assert status == 200
	assert data == { 'groups': [], 'tags': [] }


def test_staff_see_every_enabled_group_as_manageable():
	status, data = aggregate(POLICIES, ROWS, user(is_staff=True), lookup={ 5: 'readers', 6: 'curators', 7: 'others' })

	assert [(g['id'], g['manage']) for g in data['groups']] == [(6, True), (5, True)]
	assert [t['Code'] for t in data['tags']] == ['0008,1030', '0018,1030']
	assert data['tags'][1]['Group'] == { 'id': 6, 'name': 'curators' }


def test_superuser_sees_every_policy_group():
	status, data = aggregate(POLICIES, ROWS, user(is_superuser=True))

	assert len(data['groups']) == 3 and all(g['manage'] for g in data['groups'])


# Concurrent creates: the precheck passes for both, the unique constraint refuses the loser

class RacingSession(Session):
	'''	Session double whose commit refuses the insert the way PostgreSQL does once another request
		has stored the same (group, code): the first commit succeeds, the second raises.
	'''
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.commits = 0
		self.rollbacks = 0
		self.pending = []

	def add(self, obj):
		self.pending.append(obj)

	def commit(self):
		from sqlalchemy.exc import IntegrityError
		self.commits += 1
		if self.commits == 1:
			self.rows.extend(self.pending); self.pending = []
			return
		self.pending = []
		raise IntegrityError('INSERT INTO sonador_display_attribute', {}, Exception('duplicate key value violates unique constraint "uq_sonador_display_attribute_group_code"'))

	def rollback(self):
		self.rollbacks += 1
		self.pending = []


def racing_view(session):
	view = DisplayAttributeManagementView(sonador_manager=manager([{ 'group': 5, 'display_attr': True, 'display_attr_modify': True }]), sessionmaker=lambda: session)
	view.uri = '/groups/5/display-attributes'
	view.request = { 'method': 'POST', 'headers': {}, 'get': {}, 'body': '' }
	view.output = Output()
	view.json_cls = json.JSONEncoder
	view.group = group(5, 'readers')
	view.user = user(groups=[(5, 'readers')])
	# The precheck sees an empty collection for both requests, as it would when they interleave.
	view.validate_form_data = lambda s, *a, **k: DisplayAttributeValidationForm.clean(Code=view.POST['Code'], **form_kwargs(session=Session()))
	view.get_response_headers = lambda *a, **k: {}
	return view


def test_overlapping_creates_store_one_row_and_answer_the_loser_with_the_duplicate_error():
	session = RacingSession()

	first = racing_view(session); first.POST = { 'Code': '0018,1030' }
	first.post(first.output, first.uri, first.request)
	assert first.output.status == 201
	assert [r.code for r in session.rows] == ['0018,1030']

	second = racing_view(session); second.POST = { 'Code': '00181030' }
	second.post(second.output, second.uri, second.request)
	body = json.loads(second.output.body)

	assert second.output.status == 400
	assert body['status'] == 'fail'
	assert 'already part of the collection' in body['errors']['Code'][0]['message']
	assert 'duplicate key' not in second.output.body and 'INSERT' not in second.output.body
	assert [r.code for r in session.rows] == ['0018,1030']
	assert session.rollbacks == 1

	# The session is usable afterwards: a different code stores normally
	third = racing_view(session); third.POST = { 'Code': '0008,1030' }
	session.commits = 0
	third.post(third.output, third.uri, third.request)
	assert third.output.status == 201
	assert sorted(r.code for r in session.rows) == ['0008,1030', '0018,1030']
