''' Provides resource management views which proxy DICOM-UID addressed management
    operations from the DICOMweb API to the Orthanc internal API.

    DELETE of a study or series is not part of the DICOMweb standard, so these
    endpoints are hung from a "manage" path component: an extension namespace which
    distinguishes them from the standard study/series resource paths and which is
    intended to host future management operations (anonymize, modify, reindex)
    without a second naming decision.
'''
import abc, logging, json, traceback

import client.apisettings as gcapicodes
from client.errors import ResourceDoesNotExist

from sonador.serialization import SonadorJsonEncoder

from ..db.cache import CacheStudy, CacheSeries

from ..cache.web.base import CacheBaseView
from .dicomweb import DicomResourceMixin

logger = logging.getLogger(__name__)


class ManageBaseView(DicomResourceMixin, CacheBaseView):
    ''' Resource management view which resolves a DICOM UID to a Sonador cache resource
        and redirects the caller to that resource's own Orthanc API URL, where the
        management operation is performed.

        Authorization is enforced outside of this view, by the orthanc-authorization
        plugin consulting the Sonador ACL system, exactly as it is for every other
        DICOMweb route. These views verify resource existence only and deliberately
        carry no permission logic.

        Deliberately defines no get(). dispatch() resolves handlers by method name, so any
        get() added here would make GET .../manage a success rather than a 405, and would
        additionally trigger the head = get aliasing in OrthancBaseView.setup. Without one,
        _allowed_methods() correctly reports ['DELETE', 'OPTIONS'].

        @attr sonador_manager (Sonador Manager instance): Orthanc Sonador manager instance
        @attr sessionmaker (SQLAlchemy sessionmaker class): session maker instance to be
            used for creating database connections/sessions.
        @attr redirect_status_code (int): status code used for the management redirect
    '''
    # 307 preserves the request method across the redirect. RFC 7231 6.4.3 permits a user
    # agent to rewrite the method to GET on a 302, and browsers do so inconsistently for
    # methods other than GET. A DELETE rewritten to GET lands on the resource view's get()
    # handler and answers 200 with resource JSON having deleted nothing: a silent no-op
    # which reads as success. Declared as a class attribute so it remains settable through
    # as_view(), which rejects any keyword that is not already an attribute of the class.
    redirect_status_code = 307

    def setup(self, output, uri, request, *args, **kwargs):
        super().setup(output, uri, request, *args, **kwargs)

        # Ensure required components are present
        self.init_resource_mixin(*args, **kwargs)

    @abc.abstractmethod
    def get_object(self, session, *args, **kwargs):
        ''' Retrieve object instance for which the management operation should be performed.
            get_object must call get_resource, which will verify that the resource exists
            before attempting to retrieve the imaging resource instance.

            @returns sonador.imaging.orthanc.base.ImagingResource subclass
        '''

    def delete(self, output, uri, request, *args, **kwargs):
        ''' Verify that the object exists and then redirect the caller to the primary
            Orthanc resource endpoint, where the removal is performed.
        '''
        try:
            with self.sessionmaker() as session:

                # Retrieve object instance
                obj = self.get_object(session, *args, **kwargs)

            # Redirect to the resource's own Orthanc API endpoint. resource_url (not
            # filearchive_url) yields studies/{orthanc-id} or series/{orthanc-id}, which
            # is the endpoint that implements DELETE.
            return self.send_response('', status_code=self.redirect_status_code, headers={
                'Location': obj.pacs.orthanc_apiurl_fqdn(obj.resource_url, internal_dns=False)
            })

        except ResourceDoesNotExist as e:

            # Requested resource does not exist. http404_resource_not_found reads "message"
            # via kwargs.get without popping it and then forwards **kwargs to send_response,
            # which accepts no such argument; the response= form is the supported call.
            return self.http404_resource_not_found(response={gcapicodes.ERROR: str(e)})

        except Exception as err:

            # Output error to application log
            emsg = 'Unable to remove resource=%s uid=%s. Error:\n%s' % (
                self.resource_type, self.get_resource_uid(*args, **kwargs), err)
            logger.error('%s\n%s' % (emsg, traceback.format_exc()))

            # Return error message as part of response
            return self.send_response(json.dumps({
                gcapicodes.ERROR: emsg,
                gcapicodes.STATUS: gcapicodes.FAIL,
            }, cls=SonadorJsonEncoder), status_code=500)


class StudyDICOMManageView(ManageBaseView):
    ''' Management operations for a study instance. Part of the DICOMweb API within Sonador.
    '''
    resource_type = CacheStudy.type
    resource_code = CacheStudy.code

    def get_object(self, session, *args, **kwargs):
        ''' Retrieve study instance for which the management operation should be performed
        '''
        r = self.get_resource(session, *args, **kwargs)
        return self.sonador_manager.get_internal_imageserver().get_study(r.publicid)


class SeriesDICOMManageView(ManageBaseView):
    ''' Management operations for a series instance. Part of the DICOMweb API within Sonador.
    '''
    resource_type = CacheSeries.type
    resource_code = CacheSeries.code

    def get_object(self, session, *args, **kwargs):
        ''' Retrieve series instance for which the management operation should be performed
        '''
        r = self.get_resource(session, *args, **kwargs)
        return self.sonador_manager.get_internal_imageserver().get_series(r.publicid)
