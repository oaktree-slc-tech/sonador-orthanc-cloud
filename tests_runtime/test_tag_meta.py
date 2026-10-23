'''	Series Tags: `Meta` display attributes on the tag API
'''
import json

import pytest

from sonador_orthanc.db.tag import ImagingTag
from sonador_orthanc.db.helpers import orthanc_tagjson
from sonador.apisettings.display_attrs import normalize_hex_color
from sonador_orthanc.web.tag import TagItemManagementView, TagItemRestView

from test_displayattr_views import Session, user
from test_tag_policy import TAG, write_view


POLICY = [{ 'group': 5, 'tag': True, 'tag_modify': True }]
MEMBER = user(groups=[(5, 'readers')])


def create(session, body):
	view = write_view(TagItemManagementView, POLICY, MEMBER, session, 'POST', '/groups/5/tags')
	view.POST = body
	view.post(view.output, view.uri, view.request)
	return view.output


def update(session, uid, body):
	view = write_view(TagItemRestView, POLICY, MEMBER, session, 'PUT', '/groups/5/tags/%s' % uid)
	view.POST = body
	view.put(view.output, view.uri, view.request)
	return view.output


@pytest.mark.parametrize('value, expected', [
	('#8ad0a8', '#8AD0A8'), ('#FFF', '#FFFFFF'), (' #0a0 ', '#00AA00'), (None, None),
])
def test_hex_colors_are_normalized(value, expected):
	assert normalize_hex_color(value) == expected


@pytest.mark.parametrize('value', ['8AD0A8', '#8AD0A', '#GGGGGG', 'red', 'rgb(1,2,3)', '#8AD0A8FF', 12, '', '   '])
def test_invalid_hex_colors_are_refused(value):
	with pytest.raises(ValueError):
		normalize_hex_color(value)


def test_create_stores_normalized_display_colors():
	session = Session()
	meta = { 'DisplayAttributes': { 'Color': { 'Background': '#123', 'Border': '#fff', 'Value': '#8ad0a8', 'Meaning': None } } }

	output = create(session, dict(TAG, Meta=meta))

	assert output.status == 201, output.body
	assert session.rows[0].meta == { 'DisplayAttributes': { 'Color': { 'Background': '#112233', 'Border': '#FFFFFF', 'Value': '#8AD0A8' } } }
	assert orthanc_tagjson(session.rows[0])['Meta'] == session.rows[0].meta


def test_create_without_meta_stores_nothing():
	session = Session()
	assert create(session, dict(TAG)).status == 201
	assert session.rows[0].meta is None
	assert 'Meta' not in orthanc_tagjson(session.rows[0])


@pytest.mark.parametrize('meta, field', [
	({ 'Unknown': {} }, 'Meta.Unknown'),
	({ 'DisplayAttributes': { 'Font': 'Arial' } }, 'Meta.DisplayAttributes.Font'),
	({ 'DisplayAttributes': { 'Color': { 'Outline': '#FFFFFF' } } }, 'Meta.DisplayAttributes.Color.Outline'),
	({ 'DisplayAttributes': { 'Color': { 'Border': 'white' } } }, 'Meta.DisplayAttributes.Color.Border'),
	({ 'DisplayAttributes': { 'Color': { 'Value': 'green' } } }, 'Meta.DisplayAttributes.Color.Value'),
	({ 'DisplayAttributes': 'red' }, 'Meta.DisplayAttributes'),
	({ 'DisplayAttributes': { 'Color': { 'Background': '' } } }, 'Meta.DisplayAttributes.Color.Background'),
])
def test_invalid_meta_is_refused(meta, field):
	session = Session()

	output = create(session, dict(TAG, Meta=meta))

	assert output.status == 400
	assert field in json.loads(output.body)['errors']
	assert session.rows == []


def test_update_without_meta_keeps_the_stored_document():
	stored = { 'DisplayAttributes': { 'Color': { 'Value': '#8AD0A8' } } }
	existing = ImagingTag(uid='t1', group=5, value='RID1301', meaning='Lung', scheme_designator='RADLEX', meta=stored)
	session = Session([existing])

	output = update(session, 't1', dict(TAG, Meaning='Lungs'))

	assert output.status == 200, output.body
	assert (existing.meaning, existing.meta) == ('Lungs', stored)


def test_update_replaces_or_clears_meta():
	existing = ImagingTag(uid='t1', group=5, value='RID1301', meaning='Lung', scheme_designator='RADLEX',
		meta={ 'DisplayAttributes': { 'Color': { 'Value': '#8AD0A8' } } })
	session = Session([existing])

	assert update(session, 't1', dict(TAG, Meta={ 'DisplayAttributes': { 'Color': { 'Meaning': '#C0C7F8' } } })).status == 200
	assert existing.meta == { 'DisplayAttributes': { 'Color': { 'Meaning': '#C0C7F8' } } }

	assert update(session, 't1', dict(TAG, Meta={ 'DisplayAttributes': { 'Color': { 'Meaning': None } } })).status == 200
	assert existing.meta is None

	existing.meta = { 'DisplayAttributes': { 'Color': { 'Value': '#8AD0A8' } } }
	assert update(session, 't1', dict(TAG, Meta=None)).status == 200
	assert existing.meta is None
