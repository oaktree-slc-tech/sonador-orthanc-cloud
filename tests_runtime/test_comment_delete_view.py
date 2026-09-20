'''	Regression tests for the comment DELETE path: a committed removal is answered as a removal
	even when the Kafka producer refuses the removal message, and the refused message is kept for
	retry rather than discarded.
'''
import json, types

import pytest

pytest.importorskip('sqlalchemy')
pytest.importorskip('pydantic')

from sonador_orthanc.db.comments import ImagingSeriesComment
from sonador_orthanc.kafka import base as kafka_base
from sonador_orthanc.web.comments import CommentSeriesRestView


SERIES_ID = '437ce199-d7e4b001-5e779d3f-2077c411-5beab5cd'
COMMENT_ID = '7fd9db40-cd0c-4266-853e-a69cbbbce57d'
TOPIC = 'orthanc-index'


class RefusingProducer:
	'''	confluent_kafka.Producer double: `produce()` raises BufferError `refusals` times, then accepts.
	'''
	def __init__(self, config, refusals=0):
		self.refusals = refusals
		self.produced = []

	def produce(self, topic, value, *args, **kwargs):
		if self.refusals > 0:
			self.refusals -= 1
			raise BufferError('Local: Queue full')

		self.produced.append((topic, value))

	def poll(self, *args, **kwargs):
		return 0

	def flush(self, *args, **kwargs):
		return 0


class Session:
	def __init__(self):
		self.deleted = []
		self.commits = 0

	def delete(self, obj):
		self.deleted.append(obj)

	def add(self, obj):
		pass

	def commit(self):
		self.commits += 1

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False


class Output:
	'''	Orthanc REST output double: records the status and body the view answers with.
	'''
	def __init__(self):
		self.status = None
		self.body = None
		self.headers = {}

	def SetHttpHeader(self, key, value):
		self.headers[key] = value

	def SendHttpStatus(self, status, body):
		self.status, self.body = status, body

	def AnswerBuffer(self, body, mtype):
		self.status, self.body = 200, body


def make_view(producer):
	'''	A series-comment REST view wired to doubles, positioned on a DELETE of COMMENT_ID.
	'''
	session = Session()
	comment = ImagingSeriesComment(uid=COMMENT_ID, series_id=SERIES_ID, user=13, text='to be removed')

	manager = types.SimpleNamespace(kafka_producer=producer, imageserver_id='dev01')
	view = CommentSeriesRestView(sonador_manager=manager, sessionmaker=lambda: session, kafka_topic=TOPIC)
	view.uri = '/series/%s/comments/%s' % (SERIES_ID, COMMENT_ID)
	view.request = { 'method': 'DELETE', 'headers': {}, 'get': {} }
	view.output = Output()
	view.json_cls = json.JSONEncoder
	view.user = types.SimpleNamespace(pk=36, id=36, username='remover', email='remover@example.org',
		first_name='Test', last_name='Remover')

	# Database lookup and the authorization rule are exercised by the functional suite; here they
	# are held constant so the test isolates what happens after the row is committed away.
	view.get_object = lambda session, **kwargs: comment
	view.get_object_kwargs = lambda *args, **kwargs: { 'rid': SERIES_ID, 'cid': COMMENT_ID }
	view.validate_delete = lambda session, obj, *args, **kwargs: None

	return view, session, comment


@pytest.fixture
def producer(monkeypatch):
	def factory(refusals):
		monkeypatch.setattr(kafka_base, 'Producer', lambda config: RefusingProducer(config, refusals=refusals))
		return kafka_base.SonadorProducer({ 'servers': ['kafka:9092'], 'topic': TOPIC })

	return factory


def _delete(view):
	view.delete(view.output, view.uri, view.request)
	return view.output.status, json.loads(view.output.body)


def test_committed_removal_is_answered_as_removed_and_published(producer):
	view, session, comment = make_view(producer(refusals=0))

	status, body = _delete(view)

	assert status == 200
	assert body['ID'] == COMMENT_ID and body['Series'] == SERIES_ID and body['status'] == 'success'
	assert session.deleted == [comment] and session.commits == 1

	(topic, payload), = view.sonador_manager.kafka_producer.producer.produced
	message = json.loads(payload)
	assert topic == TOPIC
	assert message['operation'] == 'comment-remove.series'
	assert message['ID'] == COMMENT_ID and message['Resource'] == 'Comment'
	assert message['RemovedBy']['username'] == 'remover'


def test_enqueue_failure_after_commit_does_not_fail_the_request(producer):
	view, session, comment = make_view(producer(refusals=1))

	status, body = _delete(view)

	# The row is gone and the client is told so; the message was not lost.
	assert status == 200
	assert body['ID'] == COMMENT_ID and body['status'] == 'success'
	assert session.deleted == [comment] and session.commits == 1

	kafka_producer = view.sonador_manager.kafka_producer
	assert kafka_producer.producer.produced == []
	assert len(kafka_producer.pending) == 1
	assert json.loads(kafka_producer.pending[0][1])['ID'] == COMMENT_ID


def test_retained_removal_message_is_published_on_next_poll(producer):
	view, session, comment = make_view(producer(refusals=1))
	_delete(view)

	kafka_producer = view.sonador_manager.kafka_producer
	kafka_producer.poll(0)

	assert len(kafka_producer.pending) == 0
	(topic, payload), = kafka_producer.producer.produced
	assert json.loads(payload)['operation'] == 'comment-remove.series'


def test_producer_error_of_any_kind_does_not_fail_the_request(producer):
	view, session, comment = make_view(producer(refusals=0))

	def broken(*args, **kwargs):
		raise RuntimeError('producer gone')

	view.sonador_manager.kafka_producer.send_msg = broken

	status, body = _delete(view)

	assert status == 200 and body['ID'] == COMMENT_ID
	assert session.deleted == [comment] and session.commits == 1


def test_refused_removal_is_a_400_and_nothing_is_deleted(producer):
	from pydantic import ValidationError

	view, session, comment = make_view(producer(refusals=0))
	view.validate_delete = lambda session, obj, *args, **kwargs: (_ for _ in ()).throw(
		view.modelform.user_validation_error('not permitted', request_user=view.user))

	status, body = _delete(view)

	assert status == 400
	assert body['status'] == 'fail' and 'User' in body['errors']
	assert session.deleted == [] and session.commits == 0
	assert view.sonador_manager.kafka_producer.producer.produced == []


# ---------------------------------------------------------------------------------------
# Removal rule
# ---------------------------------------------------------------------------------------

def _comment(user=13):
	return ImagingSeriesComment(uid=COMMENT_ID, series_id=SERIES_ID, user=user, text='x')


def _user(pk):
	return types.SimpleNamespace(pk=pk, id=pk, username='user-%s' % pk)


def test_author_with_comment_edit_may_remove_own_comment():
	from sonador_orthanc.validation import CommentValidationForm

	CommentValidationForm.validate_removal(_comment(user=13), _user(13),
		resource_perms={ 'view': True, 'comment_edit': True, 'remove': False })


def test_author_without_comment_edit_may_not_remove_own_comment():
	from pydantic import ValidationError
	from sonador_orthanc.validation import CommentValidationForm

	with pytest.raises(ValidationError):
		CommentValidationForm.validate_removal(_comment(user=13), _user(13),
			resource_perms={ 'view': True, 'comment_edit': False, 'remove': False })


def test_remove_grant_lets_another_user_remove_the_comment():
	from sonador_orthanc.validation import CommentValidationForm

	CommentValidationForm.validate_removal(_comment(user=13), _user(36),
		resource_perms={ 'view': True, 'comment_edit': False, 'remove': True })


def test_comment_edit_alone_does_not_cover_another_users_comment():
	from pydantic import ValidationError
	from sonador_orthanc.validation import CommentValidationForm

	with pytest.raises(ValidationError) as excinfo:
		CommentValidationForm.validate_removal(_comment(user=13), _user(36),
			resource_perms={ 'view': True, 'comment_edit': True, 'remove': False })

	assert excinfo.value.errors()[0]['loc'] == ('User',)


def test_removal_fails_closed_without_identity_or_permissions():
	from pydantic import ValidationError
	from sonador_orthanc.validation import CommentValidationForm

	with pytest.raises(ValidationError):
		CommentValidationForm.validate_removal(_comment(user=13), None, resource_perms={ 'remove': True })

	with pytest.raises(ValidationError):
		CommentValidationForm.validate_removal(_comment(user=13), _user(13), resource_perms=None)

	# A comment with no recorded author is nobody's own; only `remove` covers it.
	with pytest.raises(ValidationError):
		CommentValidationForm.validate_removal(_comment(user=None), _user(13), resource_perms={ 'comment_edit': True })

	CommentValidationForm.validate_removal(_comment(user=None), _user(13), resource_perms={ 'remove': True })


# ---------------------------------------------------------------------------------------
# Update rule
# ---------------------------------------------------------------------------------------

def test_author_with_comment_edit_may_update_own_comment():
	from sonador_orthanc.validation import CommentValidationForm

	CommentValidationForm.validate_update_user(_comment(user=13), _user(13), resource_perms={ 'comment_edit': True })


def test_update_refuses_other_users_and_unknown_identities():
	from pydantic import ValidationError
	from sonador_orthanc.validation import CommentValidationForm

	# Another user, even with remove or modify on the resource
	with pytest.raises(ValidationError):
		CommentValidationForm.validate_update_user(_comment(user=13), _user(36),
			resource_perms={ 'comment_edit': True, 'remove': True, 'modify': True })

	# Author without comment_edit
	with pytest.raises(ValidationError):
		CommentValidationForm.validate_update_user(_comment(user=13), _user(13), resource_perms={ 'comment_edit': False })

	# No request user; no recorded author
	with pytest.raises(ValidationError):
		CommentValidationForm.validate_update_user(_comment(user=13), None, resource_perms={ 'comment_edit': True })

	with pytest.raises(ValidationError):
		CommentValidationForm.validate_update_user(_comment(user=None), _user(13), resource_perms={ 'comment_edit': True })


# ---------------------------------------------------------------------------------------
# Update path: a text-only PUT keeps the rest of the comment
# ---------------------------------------------------------------------------------------

def make_update_view(body):
	'''	A series-comment REST view positioned on a PUT of COMMENT_ID, with the author as the
		request user and comment_edit granted.
	'''
	session = Session()
	comment = ImagingSeriesComment(uid=COMMENT_ID, series_id=SERIES_ID, user=13, text='original text',
		orthanc={ 'Tag': 'qc.accept', 'Score': 3 })

	manager = types.SimpleNamespace(kafka_producer=None, imageserver_id='dev01')
	view = CommentSeriesRestView(sonador_manager=manager, sessionmaker=lambda: session, kafka_topic=None)
	view.uri = '/series/%s/comments/%s' % (SERIES_ID, COMMENT_ID)
	view.request = { 'method': 'PUT', 'headers': {}, 'get': {}, 'body': json.dumps(body) }
	view.POST = body
	view.output = Output()
	view.json_cls = json.JSONEncoder
	view.user = types.SimpleNamespace(pk=13, id=13, username='author')

	view.get_object = lambda session, **kwargs: comment
	view.get_object_kwargs = lambda *args, **kwargs: { 'rid': SERIES_ID, 'cid': COMMENT_ID }
	view.init_user_context = lambda *args, **kwargs: None
	view.get_resource_perms = lambda obj, *args, **kwargs: { 'view': True, 'comment_edit': True }

	return view, session, comment


def _put(view):
	view.put(view.output, view.uri, view.request)
	return view.output.status, json.loads(view.output.body)


def test_text_only_update_keeps_metadata_author_and_creation_time():
	view, session, comment = make_update_view({ 'Text': 'revised text' })
	comment.ctime = 'creation-time'

	status, body = _put(view)

	assert status == 200 and body['ID'] == COMMENT_ID
	assert comment.text == 'revised text'
	assert comment.orthanc == { 'Tag': 'qc.accept', 'Score': 3 }
	assert comment.user == 13 and comment.ctime == 'creation-time'
	assert session.commits == 1


def test_metadata_only_update_keeps_text():
	view, session, comment = make_update_view({ 'Meta': { 'Tag': 'qc.reject' } })

	status, body = _put(view)

	assert status == 200
	assert comment.text == 'original text'
	assert comment.orthanc == { 'Tag': 'qc.reject' }


def test_update_by_another_user_changes_nothing():
	view, session, comment = make_update_view({ 'Text': 'revised by someone else' })
	view.user = types.SimpleNamespace(pk=36, id=36, username='other')

	status, body = _put(view)

	assert status == 400 and 'User' in body['errors']
	assert comment.text == 'original text' and comment.orthanc == { 'Tag': 'qc.accept', 'Score': 3 }
	assert session.commits == 0
