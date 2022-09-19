import logging, abc, datetime
from typing import Union

from client.errors import ConfigurationError
from client.utils.object import omit, pick

from sonador.apisettings import \
	IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, IMAGING_SERVER_RESOURCE_SERIES
from sonador.serialization import dcm_str2date

from ..db.helpers import dcmquery2psqlregex
from ..dcmquery import DicomQueryMixin

from .base import OrthancBaseView
from .helpers import paginate_query_results

logger = logging.getLogger(__name__)


class DicomQueryBaseView(DicomQueryMixin, OrthancBaseView):
	'''	Base class which implements properties for querying DICOM resources
	'''

	def setup(self, output, uri, request, *args, **kwargs):
		super().setup(output, uri, request)
		self._init_dcmquery(output, uri, request, *args, **kwargs)

	def paginate_query_results(self, resources, offset: Union[int, None], limit: Union[int, None]):
		'''	Apply offsets and limits to the provided resource query.

			@input resources (sqlalchemy.orm.query.Query): Query to which the offset/limit
				should be applied to.
			@input offset (int or None): Offset to (optionally) apply to query results.
			@input limit (int or None): Limit to (optionally) apply to query results.

			@returns sqlalchemy.orm.query.Query
		'''
		return paginate_query_results(resources, offset, limit)
