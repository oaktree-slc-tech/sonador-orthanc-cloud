import logging, orthanc, posixpath

from ..db.cache import CachePatient, CacheStudy, CacheSeries
from ..db.auth import UserPatientAuth, GroupPatientAuth, UserStudyAuth, GroupStudyAuth, \
	UserSeriesAuth, GroupSeriesAuth
from ..validation.auth import AuthValidationForm, AuthExtendedValidationForm, \
	UserAclValidationForm, UserAclExtendedValidationForm, GroupAclValidationForm, GroupAclExtendedValidationForm

from ..apisettings import ORTHANC_CONFIG_SECTION_DICOMWEB
from .. import apisettings as sonador_api

from .web import AuthManagementView, AuthRestView, SonadorResourceAuthorizationView, AuthDICOMManagementView, AuthDICOMRestView

logger = logging.getLogger(__name__)


def init_auth(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize Sonador auth (authentication/authorization) subsystem
	'''
	# Patient ACL endpoints
	orthanc.RegisterRestCallback(r'/patients/([0-9a-fA-F]{8}\-?){5}/acl/user',
		AuthManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CachePatient, model=UserPatientAuth,
			modelform=UserAclValidationForm))
	orthanc.RegisterRestCallback(r'/patients/([0-9a-fA-F]{8}\-?){5}/acl/user/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
		AuthRestView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CachePatient, model=UserPatientAuth,
			modelform=UserAclValidationForm))

	orthanc.RegisterRestCallback(r'/patients/([0-9a-fA-F]{8}\-?){5}/acl/group',
		AuthManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CachePatient, model=GroupPatientAuth,
			modelform=GroupAclValidationForm))
	orthanc.RegisterRestCallback(r'/patients/([0-9a-fA-F]{8}\-?){5}/acl/group/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
		AuthRestView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CachePatient, model=GroupPatientAuth,
			modelform=GroupAclValidationForm))


	# Study ACL endpoints
	orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}/acl/user',
		AuthManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, model=UserStudyAuth,
			modelform=UserAclExtendedValidationForm))
	orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}/acl/user/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
		AuthRestView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, model=UserStudyAuth,
			modelform=UserAclExtendedValidationForm))

	orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}/acl/group',
		AuthManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, model=GroupStudyAuth,
			modelform=GroupAclExtendedValidationForm))
	orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}/acl/group/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
		AuthRestView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheStudy, model=GroupStudyAuth,
			modelform=GroupAclExtendedValidationForm))


	# Series ACL endpoints
	orthanc.RegisterRestCallback(r'/series/([0-9a-fA-F]{8}\-?){5}/acl/user',
		AuthManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, model=UserSeriesAuth,
			modelform=UserAclExtendedValidationForm))
	orthanc.RegisterRestCallback(r'/series/([0-9a-fA-F]{8}\-?){5}/acl/user/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
		AuthRestView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, model=UserSeriesAuth,
			modelform=UserAclExtendedValidationForm))

	orthanc.RegisterRestCallback(r'/series/([0-9a-fA-F]{8}\-?){5}/acl/group',
		AuthManagementView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, model=GroupSeriesAuth,
			modelform=GroupAclExtendedValidationForm))
	orthanc.RegisterRestCallback(r'/series/([0-9a-fA-F]{8}\-?){5}/acl/group/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
		AuthRestView.as_view(sonador_manager=sonador_manager,
			sessionmaker=OrthancSession, resource_cachemodel=CacheSeries, model=GroupSeriesAuth,
			modelform=GroupAclExtendedValidationForm))


	# Retrieve details about what permissions a user has been granted access to
	orthanc.RegisterRestCallback(r'/system/acl/resource',
		SonadorResourceAuthorizationView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))
