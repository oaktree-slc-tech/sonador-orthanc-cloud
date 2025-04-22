'''	Orthanc views which help with the management of the comments table.
'''
import abc, logging, json, uuid, traceback, re

from pydantic import BaseModel, constr

import client.apisettings as gcapicodes
from client.utils.object import pick, omit
from client.errors import ConfigurationError, ResourceDoesNotExist, ClientOperationError

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_IMAGE, DCMHEADER_SERIES_INSTANCE_UID, DCMHEADER_STUDY_INSTANCE_UID
from sonador.serialization import SonadorJsonEncoder

from ..db.comments import ImagingSeriesComment, ImagingStudyComment
from ..db.helpers import orthanc_commentjson
from ..validation import CommentValidationForm
from ..db.cache import CacheSeries, CacheStudy
from ..db.internal import DicomIdentifiers

from ..kafka.resource import get_study_comment_kafka_data, get_series_comment_kafka_data
from ..kafka.base import KafkaMixin

from .base import OrthancBaseView
from .cache import ResourceUidMixin
from .dicomweb import DicomResourceMixin, DicomUidJsonMixin
from .ext import ResourceChildManagementBaseView, ResourceChildBaseRestView
from .secure_user import UserContextMixin, AdminUserLookupMixin

logger = logging.getLogger(__name__)


class CommentBaseManagementView(KafkaMixin, AdminUserLookupMixin, UserContextMixin, ResourceChildManagementBaseView):
	'''	 Management view which can be used to manage comments for a resource model
	'''	
	resource_cachemodel = None
	model = None
	modelform = CommentValidationForm

	sonador_manager_required_kafka = False
	kafka_topic_required = False

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Setup worklist management view
		'''
		super().setup(output, uri, request, *args, **kwargs)

		# Initialize Kafka message push
		self._init_kafka(*args, **kwargs)

	def orthanc_objectjson(self, c):
		'''	Serialize comment to JSON, add user details to response
		'''
		# Match comment with user data

		# Comment user is the same as the request user
		if getattr(self, 'user', None) and self.user.pk == c.user:
			_user = self.user

		# User fetched as part of a lookup
		elif getattr(self, 'user_collection', None) and self.user_collection.get_modelinstance(c.user):
			_user = self.user_collection.get_modelinstance(c.user)

		# Unable to locate user instance
		else: _user = None

		_json = orthanc_commentjson(c, user=_user)
		return _json

	def get_objects(self, session, *args, ruid=None, **kwargs):
		'''	Retrieve the comment instances for the view resource
		'''
		ruid = ruid or self.get_resource_uid(*args, **kwargs)
		_comments = session.query(self.model).filter_by(**{ self.model.resource_foreignkey_attr: ruid })

		# Retrieve user details for the comments
		_user_uids = set([c.user for c in _comments])
		if _user_uids:
			setattr(self, 'user_collection', self.sonador_user_lookup(_user_uids))

		return _comments

	def init_object_model(self, ruid=None, **kwargs):
		'''	Initialize new comment model instance
		'''
		ruid = ruid or self.get_resource_uid(**kwargs)

		# Retrieve user ID to add to the comment. Comments are always associated with
		# the ID of the user which made the "create" request.
		self.init_user_context(self.request)
		return self.model(**{ 'uid': str(uuid.uuid4()), self.model.resource_foreignkey_attr: ruid, 'user': self.user.pk })

	def save_object_data(self, session, form_instance, *args, **kwargs):
		# Save to db
		obj = super().save_object_data(session, form_instance)

		if self.kafka_topic:
			self.send_kafka_msg(session, obj)

		return obj


class CommentBaseRestView(KafkaMixin, AdminUserLookupMixin, UserContextMixin, ResourceChildBaseRestView):
	'''	REST endpoint which can be used to get, put, and delete comment instances associated with a resource model
	'''
	resource_cachemodel = None
	model = None
	modelform = CommentValidationForm

	sonador_manager_required_kafka = False
	kafka_topic_required = False

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Setup worklist management view
		'''
		super().setup(output, uri, request, *args, **kwargs)

		# Initialize Kafka message push
		self._init_kafka(*args, **kwargs)

	def orthanc_objectjson(self, c):
		'''	Serialize comment to JSON, add user details to response
		'''
		# Retrieve user details from lookup collection
		if c.user and getattr(self, 'user_collection', None) and self.user_collection.get_modelinstance(c.user):
			_user = self.user_collection.get_modelinstance(c.user)

		# Unable to locate user instance
		else: _user = None

		_json = orthanc_commentjson(c, user=_user)		
		return _json

	def get_object(self, session, *args, rid=None, cid=None, **kwargs):
		'''	Retrieve a comment for the provided ID. Throws ResourceDoesNotExist
			if unable to find either the parent series or a comment with the provided
			UID associated with the series.

			@returns comment instance
		'''
		# Retrieve resource and comment UID
		r = kwargs.get('resource') or self.get_resource(session, ruid=rid)
		cid = cid or self.get_object_uid(*args, **kwargs)

		comment = session.query(self.model).filter_by(**{ self.model.resource_foreignkey_attr: r.publicid, 'uid': cid }).first()
		if not comment:
			raise ResourceDoesNotExist('Unable to retrieve comment ID=%s for Series=%s' % (cid,rid))

		# Retrieve user details for the comment
		if comment.user and not getattr(self, 'user_collection', None):
			setattr(self, 'user_collection', self.sonador_user_lookup([comment.user]))

		return comment

	def modelform_kwargs(self, **kwargs):
		'''	Add session, Sonador Manager, and user context to modelform.clean
		'''
		form_kwargs = super().modelform_kwargs(**kwargs)
		form_kwargs.update({
			**pick(kwargs, ('session', 'update', 'obj')),
			**pick(self, ('sonador_manager', 'resource_cachemodel', 'model')),
		})
		
		# Retrieve user context and add to keyword arguments
		self.init_user_context(self.request)
		form_kwargs['request_user'] = self.user
		return form_kwargs
	
	def save_object_data(self, session, obj, form_instance, *args, **kwargs):
		# Save to db
		obj = super().save_object_data(session, obj, form_instance)

		if self.kafka_topic:
			self.send_kafka_msg(session, obj)

		return obj


# Series Comments Views

class KafkaSeriesCommentMixin:
	''' Kafka Mixin which provides fetch_kafka_data for series comments
	'''
	def fetch_kafka_data(self, session, obj, *args, **kwargs):
		return get_series_comment_kafka_data(self.sonador_manager, obj.series.uid, obj.uid)


class CommentSeriesManagementView(KafkaSeriesCommentMixin, CommentBaseManagementView):
	''' REST endpoint which can be used to get and add on to the Comments list for series
	'''
	resource_cachemodel = CacheSeries
	model = ImagingSeriesComment


class CommentSeriesRestView(KafkaSeriesCommentMixin, CommentBaseRestView):
	'''	REST endpoint which can be used to get, put, and delete a specified comment from a series
	'''
	resource_cachemodel = CacheSeries
	model = ImagingSeriesComment


class CommentSeriesDICOMManagementView(DicomUidJsonMixin, DicomResourceMixin, CommentSeriesManagementView):
	'''	DICOMweb comment management view: list and create
	'''
	dicom_uid_header = DCMHEADER_SERIES_INSTANCE_UID

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self._init_dicom_json(*args, **kwargs)

	def get_objects(self, session, *args, ruid=None, **kwargs):
		'''	Retrieve the comment instances for the view resource
		'''
		r = kwargs.get('resource') or self.get_resource(session, ruid=ruid)
		_comments = session.query(self.model).filter_by(**{ self.model.resource_foreignkey_attr: r.publicid })

		# Retrieve user details for the comments
		_user_uids = set([c.user for c in _comments])
		if _user_uids:
			setattr(self, 'user_collection', self.sonador_user_lookup(_user_uids))

		return _comments


class CommentSeriesDICOMRestView(DicomUidJsonMixin, DicomResourceMixin, CommentSeriesRestView):
	'''	DICOMweb REST view: retrieve individual comment instances, update, and delete comments
	'''
	dicom_uid_header = DCMHEADER_SERIES_INSTANCE_UID

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self._init_dicom_json(*args, **kwargs)


# Study Comment Views

class KafkaStudyCommentMixin:
	''' Kafka Mixin which provides fetch_kafka_data for study comments
	'''
	def fetch_kafka_data(self, session, obj, *args, **kwargs):
		return get_study_comment_kafka_data(self.sonador_manager, obj.study.uid, obj.uid)


class CommentStudyManagementView(KafkaStudyCommentMixin, CommentBaseManagementView):
	''' REST endpoint which can be used to that get, put, and delete a specified comment from a study
	'''
	resource_cachemodel = CacheStudy
	model = ImagingStudyComment


class CommentStudyRestView(KafkaStudyCommentMixin, CommentBaseRestView):
	''' REST endpoint which can be used to that get, put, and delete a specified comment from a study
	'''
	resource_cachemodel = CacheStudy
	model = ImagingStudyComment
	

class CommentStudyDICOMManagementView(DicomUidJsonMixin, DicomResourceMixin, CommentStudyManagementView):
	'''	DICOMweb comment management view: list and create for study
	'''
	dicom_uid_header = DCMHEADER_STUDY_INSTANCE_UID

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self._init_dicom_json(*args, **kwargs)

	def get_objects(self, session, *args, ruid=None, **kwargs):
		'''	Retrieve the comment instances for the view resource
		'''
		r = kwargs.get('resource') or self.get_resource(session, ruid=ruid)
		_comments = session.query(self.model).filter_by(study_id=r.publicid)

		_user_uids = set([c.user for c in _comments])
		if _user_uids:
			setattr(self, 'user_collection', self.sonador_user_lookup(_user_uids))

		return _comments


class CommentStudyDICOMRestView(DicomUidJsonMixin, DicomResourceMixin, CommentStudyRestView):
	'''	DICOMweb REST view: retrieve individual comment instances, update, and delete comments for study
	'''
	dicom_uid_header = DCMHEADER_STUDY_INSTANCE_UID

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self._init_dicom_json(*args, **kwargs)


def init_comments(orthanc_conf, sonador_manager, OrthancSession, *args, **kwargs):
	''' Initialize comment REST endpoints
	'''
	import orthanc

	if getattr(sonador_manager, "kafka_producer", None) and getattr(sonador_manager.kafka_producer, "topic", None):
		kafka_topic = sonador_manager.kafka_producer.topic
	else:
		kafka_topic = None

	orthanc.RegisterRestCallback(r'/series/([0-9a-fA-F]{8}\-?){5}/comments',
		CommentSeriesManagementView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession, kafka_topic=kafka_topic))
	orthanc.RegisterRestCallback(r'/series/([0-9a-fA-F]{8}\-?){5}/comments/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
		CommentSeriesRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession, kafka_topic=kafka_topic))
	orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}/comments',
		CommentStudyManagementView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession, kafka_topic=kafka_topic))
	orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}/comments/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
		CommentStudyRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession, kafka_topic=kafka_topic))
