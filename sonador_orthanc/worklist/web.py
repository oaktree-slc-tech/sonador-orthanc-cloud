''' Web views which provide an API compatible with the Unified Procedure Step DICOM specification.
	UPS facilitates the retrieval of scheduled procedures via worklist queries and allows for
	modalities to report when a worklist item as been fulfilled.
'''
import json, uuid, logging

from sqlalchemy.orm import selectinload, joinedload

from client.errors import ConfigurationError, ResourceDoesNotExist
from client.utils.object import pick, omit

from sonador.apisettings import DCMHEADER_STUDY_INSTANCE_UID, DCMHEADER_SERIES_INSTANCE_UID, IMAGING_SERVER_RESOURCE_STUDY, DICOM_UID_REGEX
from sonador.serialization import SonadorJsonEncoder

from ..kafka.resource import get_study_worklist_kafka_data, get_study_comment_kafka_data
from ..kafka.base import KafkaMixin

from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.worklist import ProcedureStep, StudyReviewerWorklistItem
from ..db.comments import ImagingStudyComment
from ..db.helpers import orthanc_worklist_studyjson, dcmuid_fetch_dicomidentifier_model
from ..db.internal import Resource, DicomIdentifiers


from ..web.base import OrthancBaseView
from ..web.ext import ResourceChildManagementBaseView, ResourceChildBaseRestView
from ..web.dicomweb import CacheStudyDicomWebListView
from ..web.helpers import paginate_query_results
from ..web.secure_user import AdminUserLookupMixin, AdminGroupLookupMixin, UserContextMixin
from ..web.dicomweb import DicomResourceMixin, DicomUidJsonMixin

from ..validation.worklist import WorklistItemValidationForm
from ..validation.comments import CommentValidationForm

logger = logging.getLogger(__name__)


class ProcedureStepManagementView(OrthancBaseView):
	'''	View instance which an be used to create, retrieve, and query procedure step instances from Orthanc.
	'''
	sessionmaker = None
	limit_default = 100
	offset_default = 0

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

		if not self.sessionmaker:
			raise ConfigurationError('Unable to initialize procedure step management view, invalid session maker class.')

		# Set GET request and general query parameters
		request = request or {}
		self.GET = self.request.get('get', {})

		# Retrieve request components: limit and offset
		self.limit = int(self.GET.get('limit', self.limit_default))
		self.offset = int(self.GET.get('offset', self.offset_default))

	def get(self, output, uri, request):
		'''	Return a list of procedure step instances which match the request parameters
		'''
		return self.send_response(json.dumps({'hello': 'world'}))


class StudyReviewerWorklistJsonMixin:
	'''	Mixin class which provides methods for serializing a study reviewer worklist item to JSON
	'''
	def orthanc_objectjson(self, w):
		'''	Serialize worklist to JSON, add user and group details to the response
		'''
		# Match worklist with user data
		if getattr(self, 'user_collection', None):
			user = self.user_collection.get_modelinstance(w.user)
		else: user = None

		# Match worklist with group data
		if getattr(self, 'group_collection', None):
			group = self.group_collection.get_modelinstance(w.group)
		else: group = None

		return orthanc_worklist_studyjson(w, user=user, group=group)

	def fetch_kafka_data(self, session, sid, wid, *args, **kwargs):
		'''	Retrieve worklist item data to send to Kafka

			@input session: SQLAlchemy session instance
			@input sid (str): Orthanc study UID
			@input wid (str): Orthanc worklist UID
		'''
		return get_study_worklist_kafka_data(self.sonador_manager, sid, wid)


class StudyReviewerWorklistItemManagementView(StudyReviewerWorklistJsonMixin, KafkaMixin, 
		AdminUserLookupMixin, AdminGroupLookupMixin, UserContextMixin, ResourceChildManagementBaseView):
	'''	Management endpoint which can be used to work with study reviewer worklist items
	'''
	resource_cachemodel = CacheStudy
	model = StudyReviewerWorklistItem
	modelform = WorklistItemValidationForm

	sonador_manager_required_kafka = False
	kafka_topic_required = False

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Setup worklist management view
		'''
		super().setup(output, uri, request, *args, **kwargs)

		# Initialize Kafka message push
		self._init_kafka(*args, **kwargs)

	def get_objects(self, session, *args, ruid=None, **kwargs):
		'''	Retrieve the worklist instances for the view resource
		'''
		ruid = ruid or self.get_resource_uid(*args, **kwargs)
		_workitems = session.query(self.model).filter_by(resource=ruid)

		# Retrieve user data
		_user_uids = set([w.user for w in _workitems])
		if _user_uids:
			setattr(self, 'user_collection', self.sonador_user_lookup(_user_uids))

		# Retrieve group data
		_group_uids = set([w.group for w in _workitems])
		if _group_uids:
			setattr(self, 'group_collection', self.sonador_group_lookup(_group_uids))

		return _workitems

	def init_object_model(self, ruid=None, **kwargs):
		'''	Initialize new worklist model instance
		'''
		ruid = ruid or self.get_resource_uid(**kwargs)
		return self.model(uid=str(uuid.uuid4()), resource=ruid)

	def modelform_kwargs(self, **kwargs):
		'''	Add session, Sonador Manager, and user context to modelform.clean
		'''
		form_kwargs = super().modelform_kwargs(**kwargs)
		form_kwargs.update({
			**pick(kwargs, ('session', 'create')),
			**pick(self, ('sonador_manager', 'resource_cachemodel', 'model')),
			'parent_resource_obj': self.get_resource(**kwargs),
		})

		return form_kwargs

	def save_object_data(self, session, form_instance, *args, **kwargs):
     
		# Save worklist to db
		obj = super().save_object_data(session, form_instance, *args, **kwargs)
		
		# Get Kafka data to publish to a topic
		if self.kafka_topic:
			
			# Publish worklist data to Kafka
			self.send_kafka_msg(session, obj.study.uid, obj.uid)
			
		return obj


class StudyReviewerWorklistItemRestView(StudyReviewerWorklistJsonMixin, KafkaMixin, 
		AdminUserLookupMixin, AdminGroupLookupMixin, UserContextMixin, ResourceChildBaseRestView):
	'''	REST endpoint which can be used to retrieve details for, update, and delete study reviewer worklist items
	'''
	resource_cachemodel = CacheStudy
	model = StudyReviewerWorklistItem
	modelform = WorklistItemValidationForm
	
	comment_model = ImagingStudyComment
	comment_modelform = CommentValidationForm

	sonador_manager_required_kafka = False
	kafka_topic_required = False

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self._init_kafka(*args, **kwargs)

	def get_object(self, session, *args, rid=None, cid=None, **kwargs):
		'''	Retrieve a worklist for the provided ID. Throws ResourceDoesNotExist
			if unable to find either the parent study or a worklist with the provided UID.

			@returns worklist instance
		'''
		# Retrieve resource and worklist UID
		r = kwargs.get('resource') or self.get_resource(session, ruid=rid)
		cid = cid or self.get_object_uid(*args, **kwargs)

		worklist = session.query(self.model).filter_by(resource=r.publicid, uid=cid).first()
		if not worklist:
			raise ResourceDoesNotExist('Unable to retrieve worklist ID=%s for Study=%s' % (cid,rid))

		# Retrieve user and group details for the comment
		if worklist.user and not getattr(self, 'user_collection', None):
			setattr(self, 'user_collection', self.sonador_user_lookup([worklist.user]))
		if worklist.group and not getattr(self, 'group_collection', None):
			setattr(self, 'group_collection', self.sonador_group_lookup([worklist.group]))

		return worklist

	def modelform_kwargs(self, **kwargs):
		'''	Add session, Sonador Manager, and user context to modelform.clean
		'''
		form_kwargs = super().modelform_kwargs(**kwargs)
		
		# Check submitted form request and back-fill attributes missing from request
		# with those of the existing model instance
		_obj = kwargs.get('obj')
		if _obj:
			form_kwargs = self._backfill_object_attrs(_obj, attrs=form_kwargs)
			
		form_kwargs.update({
			**pick(kwargs, ('session', 'update', 'obj')),
			**pick(self, ('sonador_manager', 'resource_cachemodel', 'model')),
			'parent_resource_obj': self.get_resource(**kwargs),
		})
		# Logic to check if comments exist in the request
		if self.POST.get('Comment'):
			self.init_user_context(self.request)
  
		return form_kwargs

	def validate_form_data(self, session, obj, *args, **kwargs):
		# Validate worklist form
		_form = super().validate_form_data(session, obj, *args, **kwargs)
  
		# If comment in request, validate comment form
		if self.POST.get('Comment'):
			self.comment_form_instance = self.comment_modelform.clean(**{
				**self.POST.get('Comment', {}),
				'session': session,
				'sonador_manager': self.sonador_manager,
				'resource_cachemodel': self.resource_cachemodel,
				'model': self.comment_model,
				'request_user': self.user,
				'update': False
			})
			
		return _form

	def init_comment_model(self, session, ruid=None, **kwargs):
		ruid = ruid or self.get_resource_uid(**kwargs)
		
		# Check if ruid is a DICOM ID – if so, get the orthanc ID of the resource
		if DICOM_UID_REGEX.fullmatch(ruid):
			di = dcmuid_fetch_dicomidentifier_model(
				session, ruid, dicom_identifiers_model=DicomIdentifiers)
			ruid = di.resource.publicid
		
		self.init_user_context(self.request)
		return self.comment_model(**{ 'uid': str(uuid.uuid4()), self.comment_model.resource_foreignkey_attr: ruid, 'user': self.user.pk })

	def fetch_comment_kafka_data(self, session, sid, cid, *args, **kwargs):
		'''	Retrieve comment data to send to Kafka

			@input session: SQLAlchemy session instance
			@input sid (str): Orthanc study UID
			@input cid (str): Orthanc comment UID
		'''
		return get_study_comment_kafka_data(self.sonador_manager, sid, cid)

	def save_object_data(self, session, obj, form_instance):
		# Save worklist to db
		obj = super().save_object_data(session, obj, form_instance)
  
		# If comment in request, save comment to db
		if self.POST.get('Comment') and getattr(self, 'comment_form_instance', None):
			cobj = self.comment_form_instance.save(session, self.init_comment_model(session))
		else: cobj = None
		
		# Get Kafka data to publish to a topic
		if self.kafka_topic:

			# Publish worklist data to Kafka
			self.send_kafka_msg(session, obj.study.uid, obj.uid)

			# Publish comment data to Kafka
			if cobj and self.kafka_topic and getattr(self.sonador_manager, 'kafka_producer', None):

				# Retrieve comment Kafka data
				cdata = self.fetch_comment_kafka_data(session, obj.study.uid, cobj.uid)
				self.sonador_manager.kafka_producer.send_msg(json.dumps(cdata, cls=self.json_cls), topic=self.kafka_topic)
			
		return obj


class StudyReviewerWorklistItemDICOMManagementView(
		DicomUidJsonMixin, DicomResourceMixin, StudyReviewerWorklistItemManagementView):
	'''	DICOMWeb StudyReviewerWorklistItemManagementView view: list and create worklist items
	'''
	dicom_uid_header = DCMHEADER_STUDY_INSTANCE_UID

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self._init_dicom_json(*args, **kwargs)


class StudyReviewerWorklistItemDICOMRestView(
		DicomUidJsonMixin, DicomResourceMixin, StudyReviewerWorklistItemRestView):
	'''	DICOMweb REST view: retrieve individual worklist details, udpate, and delete items
	'''
	dicom_uid_header = DCMHEADER_STUDY_INSTANCE_UID

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)
		self._init_dicom_json(*args, **kwargs)


class StudyReviewerWorklistItemDICOMListView(AdminUserLookupMixin, AdminGroupLookupMixin, CacheStudyDicomWebListView):
	'''	Study reviewer worklist: DICOMWeb REST endpoint. Retrieves the worklist items for the
		groups associated with the current user.
	'''
	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request, *args, **kwargs)

		# URL query parameters to filter by user and group
		self.user_query = self.GET.get('User')
		self.group_query = self.GET.get('Group')		

	def apply_worklist_queryfilter(self, dcm_resources):
		'''	Filter the provided DICOM resources to only those with worklist items that include groups 
			of which the current user is a member.
		'''
		# Model aliases
		cs = self.resource_model
		w = self.resource_model.worklist_reviewer_model

		# Filter worklists by the groups the request user is a member of
		_groupfilter = None
		for g in getattr(self.user, 'groups', []):

			_gid = g.get('id')
			if isinstance(_gid, int):

				# Create group filter
				if _groupfilter is None: _groupfilter = w.group == _gid
				else: _groupfilter |= w.group == _gid

		# Apply user's group membership filter
		if _groupfilter is not None:
			dcm_resources = dcm_resources.filter(cs.worklist_reviewer.any(_groupfilter))

		# If the user is not a member of any groups, return an empty queryset
		else: dcm_resources = dcm_resources.none()

		# Filter by user (provided via request query parameters)
		if self.user_query is not None:
			dcm_resources = dcm_resources.filter(w.user == int(self.user_query))

		# Apply additional group filter (provided via request query parameters)
		if self.group_query is not None:
			dcm_resources = dcm_resources.filter(w.group == int(self.group_query))

		return dcm_resources

	def get_base_resourcelist(self, session, *args, **kwargs):
		'''	Create a combined ORM view which contains both the cached study and the worklist fields
		'''
		# Create model aliases		
		cs = self.resource_model
		w = self.resource_model.worklist_reviewer_model

		# Join both study and workilist properties into a single view, add user and group IDs to
		# the row/result to allow for easier lookup of the user/group IDs
		dweb_studies = session.query(cs, w, w.group, w.user) \
			.join(w, cs.uid == w.resource)

		return self.apply_worklist_queryfilter(dweb_studies)

	def get_studylist(self, session, *args, **kwargs):
		''' Retrieve studies from database
		'''
		dweb_studies = super().get_studylist(session, *args, **kwargs)		

		# Retrieve user data
		_user_uids = set([_dcm.user for _dcm in dweb_studies[self.offset:self.limit+self.offset]])
		if _user_uids:
			setattr(self, 'user_collection', self.sonador_user_lookup(_user_uids))

		# Retrieve group data
		_group_uids = set([_dcm.group for _dcm in dweb_studies[self.offset:self.limit+self.offset]])
		if _group_uids:
			setattr(self, 'group_collection', self.sonador_group_lookup(_group_uids))

		return dweb_studies

	def dcmweb_studyjson(self, result):
		'''	Combine study, patient, and worklist metadata
		'''
		# Match worklist with user data
		if getattr(self, 'user_collection', None):
			user = self.user_collection.get_modelinstance(result.user)
		else: user = None

		# Match worklist with group data
		if getattr(self, 'group_collection', None):
			group = self.group_collection.get_modelinstance(result.group)
		else: group = None

		# Unpack cache study and worklist references from result
		cs = result[0]
		w = result[1]

		# Serialize worklist to JSON and add study tags
		dcm = orthanc_worklist_studyjson(w, user=user, group=group)
		dcm.update(super().dcmweb_studyjson(cs))

		return dcm