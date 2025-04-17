''' Web views which provide an API compatible with the Unified Procedure Step DICOM specification.
	UPS facilitates the retrieval of scheduled procedures via worklist queries and allows for
	modalities to report when a worklist item as been fulfilled.
'''
import json, uuid, logging

from sqlalchemy.orm import selectinload, joinedload

from client.errors import ConfigurationError, ResourceDoesNotExist
from client.utils.object import pick, omit

from sonador.apisettings import DCMHEADER_STUDY_INSTANCE_UID, DCMHEADER_SERIES_INSTANCE_UID

from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.worklist import ProcedureStep, StudyReviewerWorklistItem
from ..db.helpers import orthanc_worklist_studyjson

from ..web.base import OrthancBaseView
from ..web.ext import ResourceChildManagementBaseView, ResourceChildBaseRestView
from ..web.dicomweb import CacheStudyDicomWebListView
from ..web.helpers import paginate_query_results
from ..web.secure_user import AdminUserLookupMixin, AdminGroupLookupMixin
from ..web.dicomweb import DicomResourceMixin, DicomUidJsonMixin

from ..validation.worklist import WorklistItemValidationForm

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


class StudyReviewerWorklistItemManagementView(StudyReviewerWorklistJsonMixin, AdminUserLookupMixin, AdminGroupLookupMixin, 
		ResourceChildManagementBaseView):
	'''	Management endpoint which can be used to work with study reviewer worklist items
	'''
	resource_cachemodel = CacheStudy
	model = StudyReviewerWorklistItem
	modelform = WorklistItemValidationForm

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


class StudyReviewerWorklistItemRestView(StudyReviewerWorklistJsonMixin, AdminUserLookupMixin, AdminGroupLookupMixin,
		ResourceChildBaseRestView):
	'''	REST endpoint which can be used to retrieve details for, update, and delete study reviewer worklist items
	'''
	resource_cachemodel = CacheStudy
	model = StudyReviewerWorklistItem
	modelform = WorklistItemValidationForm

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
		return form_kwargs

	# TODO: Add logic to allow for comments to be attached to a study worklist as part
	# of an update.


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