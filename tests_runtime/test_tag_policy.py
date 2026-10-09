'''	Series Tags: the group policy write rule on the tag form and views
'''
import json, types

import pytest
from pydantic import ValidationError as PydanticValidationError

from sonador_orthanc.db.tag import ImagingTag
from sonador_orthanc.validation.tag import TagValidationForm
from sonador_orthanc.web.tag import TagItemManagementView, TagItemRestView

from test_displayattr_views import Session, Output, manager, user, group


TAG = { 'Value': 'RID1301', 'Meaning': 'Lung', 'SchemeDesignator': 'RADLEX' }


def form_kwargs(policies, requester, session=None, obj=None):
	return { 'sonador_manager': manager(policies), 'session': session if session is not None else Session(), 'model': ImagingTag,
		'group': group(5, 'readers'), 'create': obj is None, 'update': obj is not None, 'obj': obj, 'request_user': requester }


@pytest.mark.parametrize('policy, requester, allowed', [
	({ 'group': 5, 'tag': True, 'tag_modify': True }, user(groups=[(5, 'readers')]), True),
	({ 'group': 5, 'tag': True, 'tag_modify': False }, user(groups=[(5, 'readers')]), False),
	({ 'group': 5, 'tag': True, 'tag_modify': True }, user(groups=[(6, 'curators')]), False),
	({ 'group': 5, 'tag': False, 'tag_modify': True }, user(groups=[(5, 'readers')]), False),
	# Sonador has no staff rule for Series Tags: staff need membership and the modify flag like anyone
	({ 'group': 5, 'tag': True, 'tag_modify': False }, user(is_staff=True), False),
	({ 'group': 5, 'tag': True, 'tag_modify': False }, user(groups=[(5, 'readers')], is_staff=True), False),
	({ 'group': 5, 'tag': True, 'tag_modify': True }, user(groups=[(5, 'readers')], is_staff=True), True),
	({ 'group': 5, 'tag': True, 'tag_modify': False }, user(is_superuser=True), True),
	({ 'group': 5, 'tag': False, 'tag_modify': True }, user(is_superuser=True), False),
	({ 'group': 5, 'tag': True, 'tag_modify': True }, None, False),
])
def test_tag_form_applies_the_policy_write_rule(policy, requester, allowed):
	kwargs = form_kwargs([policy], requester)

	if allowed:
		assert TagValidationForm.clean(**TAG, **kwargs).Value == 'RID1301'
	else:
		with pytest.raises(PydanticValidationError) as err:
			TagValidationForm.clean(**TAG, **kwargs)
		assert [e['loc'] for e in err.value.errors()] == [('User',)]


def test_tag_form_still_validates_fields_after_the_rule():
	with pytest.raises(PydanticValidationError) as err:
		TagValidationForm.clean(Value='', Meaning='Lung', SchemeDesignator='RADLEX',
			**form_kwargs([{ 'group': 5, 'tag': True, 'tag_modify': True }], user(is_superuser=True)))
	assert ('Value',) in [e['loc'] for e in err.value.errors()]


def write_view(cls, policies, requester, session, method, uri):
	view = cls(sonador_manager=manager(policies), sessionmaker=lambda: session)
	view.uri = uri
	view.request = { 'method': method, 'headers': {}, 'get': {}, 'body': '' }
	view.output = Output()
	view.json_cls = json.JSONEncoder
	view.group = group(5, 'readers')
	view.user = requester
	view.get_response_headers = lambda *a, **k: {}
	return view


def test_tag_create_endpoint_refuses_a_disabled_policy_for_a_superuser():
	session = Session()
	view = write_view(TagItemManagementView, [{ 'group': 5, 'tag': False, 'tag_modify': True }], user(is_superuser=True), session,
		'POST', '/groups/5/tags')
	view.POST = dict(TAG)

	view.post(view.output, view.uri, view.request)

	assert view.output.status == 400
	assert 'User' in json.loads(view.output.body)['errors']
	assert session.rows == []


def test_tag_create_endpoint_stores_for_a_member_with_the_modify_flag():
	session = Session()
	view = write_view(TagItemManagementView, [{ 'group': 5, 'tag': True, 'tag_modify': True }], user(groups=[(5, 'readers')]), session,
		'POST', '/groups/5/tags')
	view.POST = dict(TAG)

	view.post(view.output, view.uri, view.request)

	assert view.output.status == 201, view.output.body
	assert [r.value for r in session.rows] == ['RID1301']


def test_tag_delete_endpoint_applies_the_policy_write_rule():
	existing = ImagingTag(uid='t1', group=5, value='RID1301', meaning='Lung', scheme_designator='RADLEX')
	session = Session([existing])

	refused = write_view(TagItemRestView, [{ 'group': 5, 'tag': True, 'tag_modify': False }], user(groups=[(5, 'readers')]), session,
		'DELETE', '/groups/5/tags/t1')
	refused.delete(refused.output, refused.uri, refused.request)
	assert refused.output.status == 400
	assert session.rows == [existing]

	allowed = write_view(TagItemRestView, [{ 'group': 5, 'tag': True, 'tag_modify': True }], user(groups=[(5, 'readers')], is_staff=True), session,
		'DELETE', '/groups/5/tags/t1')
	allowed.delete(allowed.output, allowed.uri, allowed.request)
	assert allowed.output.status == 200, allowed.output.body
	assert session.rows == []


# The collection header reports what the write rule allows

@pytest.mark.parametrize('policy, requester, expected', [
	({ 'group': 5, 'tag': True, 'tag_modify': True }, user(groups=[(5, 'readers')]), { 'tag': True, 'tag_modify': True }),
	({ 'group': 5, 'tag': True, 'tag_modify': False }, user(groups=[(5, 'readers')]), { 'tag': True, 'tag_modify': False }),
	({ 'group': 5, 'tag': True, 'tag_modify': False }, user(is_staff=True), { 'tag': True, 'tag_modify': False }),
	({ 'group': 5, 'tag': True, 'tag_modify': False }, user(groups=[(5, 'readers')], is_staff=True), { 'tag': True, 'tag_modify': False }),
	({ 'group': 5, 'tag': True, 'tag_modify': True }, user(groups=[(5, 'readers')], is_staff=True), { 'tag': True, 'tag_modify': True }),
	({ 'group': 5, 'tag': False, 'tag_modify': True }, user(groups=[(5, 'readers')]), { 'tag': False, 'tag_modify': False }),
	({ 'group': 5, 'tag': False, 'tag_modify': True }, user(is_superuser=True), { 'tag': True, 'tag_modify': False }),
])
def test_tag_header_matches_the_write_rule(policy, requester, expected):
	view = write_view(TagItemManagementView, [policy], requester, Session(), 'GET', '/groups/5/tags')
	view.get_response_headers = TagItemManagementView.get_response_headers.__get__(view)

	headers = view.get_response_headers([], 201, 'GET')

	assert json.loads(headers['sonador-permissions']) == expected

	create = write_view(TagItemManagementView, [policy], requester, Session(), 'POST', '/groups/5/tags')
	create.POST = dict(TAG)
	create.post(create.output, create.uri, create.request)
	assert (create.output.status == 201) == expected['tag_modify']


def test_read_only_staff_member_is_reported_and_refused_consistently():
	'''	A staff user who belongs to a group whose policy enables Series Tags without the modify flag:
		Sonador authorizes the collection read and denies the writes (it has no staff rule for Series
		Tags), so the header must say tag_modify=false and the plugin must refuse the writes too.
	'''
	policy = { 'group': 5, 'tag': True, 'tag_modify': False }
	requester = user(groups=[(5, 'readers')], is_staff=True)
	existing = ImagingTag(uid='t1', group=5, value='RID1301', meaning='Lung', scheme_designator='RADLEX')
	session = Session([existing])

	view = write_view(TagItemManagementView, [policy], requester, session, 'GET', '/groups/5/tags')
	view.get_response_headers = TagItemManagementView.get_response_headers.__get__(view)
	assert json.loads(view.get_response_headers([], 201, 'GET')['sonador-permissions']) == { 'tag': True, 'tag_modify': False }

	create = write_view(TagItemManagementView, [policy], requester, session, 'POST', '/groups/5/tags')
	create.POST = dict(TAG, Value='RID1302')
	create.post(create.output, create.uri, create.request)

	update = write_view(TagItemRestView, [policy], requester, session, 'PUT', '/groups/5/tags/t1')
	update.POST = dict(TAG, Meaning='Renamed')
	update.put(update.output, update.uri, update.request)

	assert (create.output.status, update.output.status) == (400, 400)
	assert session.rows == [existing] and existing.meaning == 'Lung'
