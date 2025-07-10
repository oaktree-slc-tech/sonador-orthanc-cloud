
''' Provides download views which can be used to proxy data from the Orthanc
    internal API to the DICOMweb API.
'''
import abc, logging, io, posixpath, pydicom, json, copy, datetime, traceback, uuid, zipfile

import client.apisettings as gcapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist

from sonador.serialization import SonadorJsonEncoder

from ..db.cache import CacheStudy, CacheSeries

from ..cache.web.base import CacheBaseView
from .dicomweb import DicomResourceMixin

logger = logging.getLogger(__name__)


class DownloadBaseView(DicomResourceMixin, CacheBaseView):
    ''' Download zip archive of series/study. Inherits from CacheBaseView,
        which provides check for a Sonador manager instance and a session maker.

        @attr sonador_manager (Sonador Manager instance): Orthanc Sonador mangaer instance
        @attr sessionmaker (SQLAlchemy sessionmaker class): session maker instance to be
            used for creating database connections/sessions.
    '''
    def setup(self, output, uri, request, *args, **kwargs):
        super().setup(output, uri, request, *args, **kwargs)

        # Ensure required components are present
        self.init_resource_mixin(*args, **kwargs)

    @abc.abstractmethod
    def get_object(self, session, *args, **kwargs):
        ''' Retrieve object instance for which the zip archive should be fetched.
            get_object must call get_resource, which will verify that the resource
            exists before attempting to retrieve the file archive instance.

            @returns sonador.imaging.orthanc.base.ImagingResource sublass
        '''

    def get(self, output, uri, request, *args, **kwargs):
        ''' Verify that the object exists and then redirect user to primary Orthanc download endpoint
        '''
        try:
            with self.sessionmaker() as session:

                # Retrieve object instance
                obj = self.get_object(session, *args, **kwargs)
                
            # Redirect to study download endpoint
            return self.send_response('', status_code=302, headers={
                'Location': obj.pacs.orthanc_apiurl_fqdn(obj.filearchive_url, internal_dns=False)
            })

        except ResourceDoesNotExist as e:

            # Requested resource does not exist
            return self.http404_resource_not_found(message=str(e))

        except Exception as err:

            # Output error to application log
            emsg = 'Unable to download archive for resource=%s uid=%s. Error:\n%s' % (
                self.resource_type, self.get_resource_uid(*args, **kwargs), err)
            logger.error('%s\n%s' % (emsg, traceback.format_exc()))
            
            # Return error message as part of response
            return self.send_response(json.dumps({
                gcapicodes.ERROR: emsg, 
                gcapicodes.STATUS: gcapicodes.FAIL,
            }, cls=SonadorJsonEncoder), status_code=500)


class StudyDICOMDownloadView(DownloadBaseView):
    ''' Download zip archive for a study instance. Part of DICOMweb API within Sonador.
    '''
    resource_type = CacheStudy.type
    resource_code = CacheStudy.code

    def get_object(self, session, *args, **kwargs):
        ''' Retrieve study instance for which the zip archive should be fetched
        '''
        r = self.get_resource(session, *args, **kwargs)
        return self.sonador_manager.get_internal_imageserver().get_study(r.publicid)


class SeriesDICOMDownloadView(DownloadBaseView):
    ''' Download zip archive for a series instance. Part of DICOMWeb API within Sonador.
    '''
    resource_type = CacheSeries.type
    resource_code = CacheSeries.code

    def get_object(self, session, *args, **kwargs):
        ''' Retrieve series instance for which the zip aarchive should be fetched
        '''
        r = self.get_resource(session, *args, **kwargs)
        return self.sonador_manager.get_internal_imageserver().get_series(r.publicid)
