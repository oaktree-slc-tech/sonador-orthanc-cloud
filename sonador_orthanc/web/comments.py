'''	Orthanc views which help with the management of the comments table.
'''
import logging, json, uuid, traceback, re

from pydantic import BaseModel, constr

import client.apisettings as gcapicodes
from client.errors import ConfigurationError, ResourceDoesNotExist

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_IMAGE, DCMHEADER_SERIES_INSTANCE_UID
from sonador.serialization import SonadorJsonEncoder

from ..db.comments import ImagingSeriesComment
from ..db.helpers import orthanc_commentjson
from ..validation import CommentValidationForm
from ..db.cache import CacheSeries
from ..db.internal import DicomIdentifiers

from .base import OrthancBaseView
from .cache import ResourceUidMixin
from .dicomweb import DicomResourceMixin

logger = logging.getLogger(__name__)


class CommentSeriesManagementView(ResourceUidMixin, OrthancBaseView):
	'''	REST endpoint which can be used to get and add on to the Comments list for series
	'''
	sessionmaker = None

	resource_cachemodel = CacheSeries
	comment_model = ImagingSeriesComment
	modelform = CommentValidationForm

	orthanc_commentjson = lambda _,c: orthanc_commentjson(c)

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Verify that database properties, database models, and indexing method have been provided.
		'''
		super().setup(output, uri, request)

		if not callable(self.orthanc_commentjson):
			raise ConfigurationError(
				'Unable to initialize %s view instance: `orthanc_commentjson` is not a callable function.')

		# Ensure valid session maker instance is present
		if self.sessionmaker is None:
			raise ConfigurationError(
				'Unable to initialize %s view instance: invalid session maker instance' % type(self).__name__)
		
		# De-serialize request data and retrieve operation parameters
		self.POST = json.loads(request.get('body')) if request.get('body') else {}

		# Ensure that a resource model has been defined and an index method is available
		self.init_resource_mixin(*args, **kwargs)

	def get_comments(self, session, *args, ruid=None, **kwargs):
		'''	Retrieve the comment instances for the view resource
		'''
		ruid = ruid or self.get_resource_uid(*args, **kwargs)
		return session.query(self.comment_model).filter_by(series_id=ruid)

	def get(self, output, uri, request, *args, **kwargs):
		''' Retrieve Comments list from series
		'''
		# Parse the resource UID from the URL
		sid = self.get_resource_uid(*args, **kwargs)

		try:

			# Retrieve comments for the series
			with self.sessionmaker() as session:

				# Retrieve resource model from database (checks to see if resource exists)
				r = self.get_resource(session, *args, ruid=sid, **kwargs)
				
				# Query database for comments related to the current series
				return self.send_response(json.dumps(
					[self.orthanc_commentjson(c) for c in self.get_comments(session, ruid=sid)],
					cls=SonadorJsonEncoder))

		except ResourceDoesNotExist as err:			
			response = {
				gcapicodes.ERROR: 'Comments for Resource sid=%s does not exist' % sid or '(none)',
				gcapicodes.STATUS: gcapicodes.FAIL
			}

			return self.http404_resource_not_found(response=response)
	
	def post(self, output, uri, request, *args, **kwargs):
		''' Append new comment to the Comments list in series
		'''	
		try:
			with self.sessionmaker() as session:

				# Create comment for series
				r = self.get_resource(session, *args, **kwargs)
				comment = self.modelform.clean(**self.POST).save(
					session, self.comment_model(uid=str(uuid.uuid4()), series_id=r.publicid))

				return self.send_response(json.dumps({
					'ID': comment.uid, gcapicodes.STATUS: gcapicodes.SUCCESS, 
					gcapicodes.OBJECT_DATA: self.orthanc_commentjson(comment),
				}, cls=SonadorJsonEncoder), status_code=201)

		except ResourceDoesNotExist as err:
			return self.http404_resource_not_found(response={
				gcapicodes.ERROR: 'Resource uid=%s does not exist' % self.get_resource_uid(*args, **kwargs) or '(none)',
				gcapicodes.STATUS: gcapicodes.FAIL
			})

		except Exception as err:
			logger.error('Unable to create comment due to an error. Error: "%s"\n%s' % (err, traceback.format_exc()))

			return self.send_response(json.dumps({
				'error': str(err), gcapicodes.STATUS: gcapicodes.FAIL,
			}), status_code=400)


class CommentSeriesRestView(ResourceUidMixin, OrthancBaseView):
	'''	REST endpoint which can be used to get, put, and delete a specified comment from a series
	'''
	sessionmaker = None

	resource_cachemodel = CacheSeries
	comment_model = ImagingSeriesComment
	modelform = CommentValidationForm

	orthanc_commentjson = lambda _,c: orthanc_commentjson(c)

	def get_comment_uid(self, *args, sep='/', **kwargs):
		'''	Retrieve the UID of the comment from the URL
		'''
		return self.uri.split(sep)[-1]

	def get_comment(self, session, *args, sid=None, cid=None, **kwargs):
		'''	Retrieve a comment for the provided ID. Throws ResoruceDoesNotExist
			if unable to find either the parent series or a comment with the provided
			UID associated with the series.

			@returns comment instance
		'''
		# Retrieve resource and comment UID
		r = self.get_resource(session, *args, ruid=sid, **kwargs)
		cid = cid or self.get_comment_uid(*args, **kwargs)

		comment = session.query(self.comment_model).filter_by(series_id=r.publicid, uid=cid).first()
		if not comment:
			raise ResourceDoesNotExist('Unable to retrieve comment ID=%s for Series=%s' % (cid,sid))

		return comment

	def setup(self, output, uri, request, *args, **kwargs):
		'''	Verify that database properties, database models, and indexing method have been provided.
		'''
		super().setup(output, uri, request)

		# Ensure valid session maker instance is present
		if self.sessionmaker is None:
			raise ConfigurationError(
				'Unable to initialize %s view instance: invalid session maker instance' % type(self).__name__)
		
		# De-serialize request data and retrieve operation parameters
		self.POST = json.loads(request.get('body')) if request.get('body') else {}

		# Ensure that a resource model has been defined and an index method is available
		self.init_resource_mixin(*args, **kwargs)

	def get(self, output, uri, request, *args, **kwargs):
		''' Retrieve specific comment from series
		'''
		sid = self.get_resource_uid(*args, **kwargs)
		cid = self.get_comment_uid(*args, **kwargs)

		try:
			with self.sessionmaker() as session:

				# Retrieve requested comment
				return self.send_response(
					json.dumps(self.orthanc_commentjson(
						self.get_comment(session, *args, sid=sid, cid=cid, *args, **kwargs)), 
					cls=SonadorJsonEncoder))

		except ResourceDoesNotExist as err:
			response = ({
				gcapicodes.ERROR: 'Comment ID=%s for Series=%s does not exist' % (cid or '(none)', sid or '(none)'),
				gcapicodes.STATUS: gcapicodes.FAIL
			})
			return self.http404_resource_not_found(response=response)

	def put(self, output, uri, request, *args, **kwargs):
		''' Edit comment text data
		'''
		sid = self.get_resource_uid(*args, **kwargs)
		cid = self.get_comment_uid(*args, **kwargs)
		
		try: 
			with self.sessionmaker() as session:

				# Retrieve comment instance from database
				comment = self.get_comment(session, *args, sid=sid, cid=cid, **kwargs)

				# Parse/validate request, update model properties, commit to database
				self.modelform.clean(**self.POST).save(session, comment)
				
				return self.send_response(
					json.dumps({
						'ID': cid,
						IMAGING_SERVER_RESOURCE_SERIES: sid,
						gcapicodes.STATUS: gcapicodes.SUCCESS,
					}, cls=SonadorJsonEncoder))

		except ResourceDoesNotExist as err:
			response = ({
				gcapicodes.ERROR: 'Resource uid=%s does not exist' \
					% self.get_resource_uid(*args, **kwargs) or '(none)',
				gcapicodes.STATUS: gcapicodes.FAIL
			})

			return self.http404_resource_not_found(response=response)

		except Exception as err:
			logger.error('Unable to update series=%s comment=%s due to error. Error: %s\n%s'
				% (sid, cid, err, traceback.format_exc()))

			return self.send_response(json.dumps({
				'error': str(err), gcapicodes.STATUS: gcapicodes.FAIL
			}), status_code=400)
		
	def delete(self, output, uri, request, *args, **kwargs):
		''' Delete comment from series
		'''
		sid = self.get_resource_uid(*args, **kwargs)
		cid = self.get_comment_uid(*args, **kwargs)

		try: 
			with self.sessionmaker() as session:

				# Delete object from database
				comment = self.get_comment(session, *args, sid=sid, cid=cid, **kwargs)
				session.delete(comment)
				session.commit()

				return self.send_response(json.dumps({
					'ID': cid,
					IMAGING_SERVER_RESOURCE_SERIES: sid,
					gcapicodes.STATUS: gcapicodes.SUCCESS,
				}, cls=SonadorJsonEncoder))

		except ResourceDoesNotExist as err:
			response = ({
				gcapicodes.ERROR: 'Resource uid=%s does not exist' \
					% self.get_resource_uid(*args, **kwargs) or '(none)',
				gcapicodes.STATUS: gcapicodes.FAIL
			})

			return self.http404_resource_not_found(response=response)


class DicomSeriesJsonMixin(object):
	'''	Mixin class which adds the SeriesInstanceUID to comment JSON attributes
	'''
	def orthanc_commentjson(self, c, *args, **kwargs):
		'''	Add the DICOM series instance UID to the JSON response
		'''
		cjson = super().orthanc_commentjson(c)
		cjson[DCMHEADER_SERIES_INSTANCE_UID] = self.get_resource_uid(*args, **kwargs)
		return cjson


class CommentSeriesDICOMManagementView(DicomSeriesJsonMixin, DicomResourceMixin, CommentSeriesManagementView):
	'''	DICOMweb comment management view: list and create
	'''
	def get_comments(self, session, *args, ruid=None, **kwargs):
		'''	Retrieve the comment instances for the view resource
		'''
		r = self.get_resource(session, *args, ruid=ruid, **kwargs)
		return session.query(self.comment_model).filter_by(series_id=r.publicid)


class CommentSeriesDICOMRestView(DicomSeriesJsonMixin, DicomResourceMixin, CommentSeriesRestView):
	'''	DICOMweb REST view: retrieve individual comment instances, update, and delete comments
	'''