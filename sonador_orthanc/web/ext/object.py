'''	Orthanc view instances to help with the management of DICOM extension models not
	associated with a parent resource. View instance inherit from ext.base.ObjectManagementBaseView 
	and ext.base.ObjectBaseRestView.

	The views of this module delegate data validation and persistence to Pydantic "form"
	classes. Refer to the sonador_orthanc.validation module for more detail.
'''
import abc, logging, posixpath, pydicom, json, copy, datetime, traceback, uuid

from pydantic import ValidationError as PydanticValidationError

import client.apisettings as gcapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist
from client.utils.object import pick

from ..base import OrthancBaseView

from .base import ObjectViewMixin, ObjectManagementBaseView, ObjectBaseRestView

logger = logging.getLogger(__name__)


class ObjectManagementView(ObjectManagementBaseView):
	'''	Orthanc view instance which can be used to manage objects.
	'''
	def err_404(self, *args, **kwargs):
		'''	Create 404 error message for the view instance
		'''
		return 'Resource="%s" does not exist' % self.uri

	def syslog_err_validation(self, err, *args, **kwargs):
		'''	Create system log validation error
		'''
		return 'Unable to create object for model "%s" due to a form validation error. Error:\n%s' % (
			self.model.__name__, err
		)

	def syslog_exception(self, err, *args, **kwargs):
		'''	Create system log error for exception
		'''
		return 'Unable to process operation for model "%s" due to an error. Error: "%s"\n%s' % (
			self.model.__name__, err, traceback.format_exc()
		)


class ObjectRestView(ObjectBaseRestView):
	'''	Orthanc view instance which can be used to manage existing instances of an object.
	'''
	def err_404(self, *args, **kwargs):
		'''	Create 404 error message for the view instance
		'''
		return 'Resource="%s" does not exist' % self.uri
 
	def get_object_kwargs(self, *args, **kwargs):
		'''	Add keyword arguments to the get_object method of the view
		'''
		return { 'uid': self.get_object_uid(*args, **kwargs) }

	def syslog_err_validation(self, err, *args, **kwargs):
		'''	Create system log validation error
		'''
		return 'Unable to update child object "%s" due to a form validation error. Error:\n%s' % (
			self.model.__name__, err
		)

	def syslog_exception(self, err, *args, **kwargs):
		'''	Create system exception error
		'''
		if kwargs.get('update'):

			# Retrieve resource and object UID from URL
			getobj_kwargs = self.get_object_kwargs(*args, **kwargs)
			uid = kwargs.get('uid')

			return 'Unable to update %s=%s due to error. Error: %s\n%s' % (
				self.model.type, uid, err, traceback.format_exc()
			)

		raise ValueError('Unable to template syslog error, unknown request type')