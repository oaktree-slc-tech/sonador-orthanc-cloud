''' Web views which provide an API compatible with the Unified Procedure Step DICOM specification.
	UPS facilitates the retrieval of scheduled procedures via worklist queries and allows for
	modalities to report when a worklist item as been fulfilled.
'''
import json, uuid, logging, traceback
import client.apisettings as gcapicodes

from pydantic import ValidationError as PydanticValidationError

from client.errors import ConfigurationError, ResourceDoesNotExist
from sonador.serialization import SonadorJsonEncoder

from ..db.preferences import UserPreferences
from ..db.helpers import orthanc_user_preferences

from ..web.base import OrthancBaseView
from ..web.helpers import paginate_query_results
from ..web.ext import ObjectManagementView, ObjectRestView

from ..validation.base import OrthancViewValidationMixin
from ..validation.preferences import UserPreferencesValidationForm

from .cache import CacheBaseView, ResourceUidMixin
from .dicomweb import DicomResourceMixin, DicomUidJsonMixin

logger = logging.getLogger(__name__)


class UserPreferencesManagementView(ObjectManagementView):
	'''	View instance which an be used to create and get device resources from Orthanc.
	'''
	sessionmaker = None
	model = UserPreferences
	modelform = UserPreferencesValidationForm

	orthanc_objectjson = lambda _,pref: orthanc_user_preferences(pref)
	
	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)

class UserPreferencesRestView(ObjectRestView):
	'''	REST endpoint which can be used to get and put user preferences from Orthanc
	'''
	sessionmaker = None

	model = UserPreferences
	modelform = UserPreferencesValidationForm
	
	success_status_code = 201
	error_status_code = 400

	orthanc_objectjson = lambda _,pref: orthanc_user_preferences(pref)

	def get_object(self, session, *args, uid=None, **kwargs):
		'''	Retrieve a user preference for the provided ID. Throws ResourceDoesNotExist
			if unable to find data with the provided
			UID.

			@returns user preferences instance
		'''
        
		# Retrieve user preferences UID
		user = uid or self.get_object_uid(*args, **kwargs)

		pref = session.query(self.model).filter_by(user=user).first()
  
		if not pref:
			raise ResourceDoesNotExist('Unable to retrieve user preferences ID=%s' % uid)

		return pref