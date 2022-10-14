'''	Utility functions and utilities related to the web module
'''
from typing import Union


def paginate_query_results(resources, offset: Union[int, None], limit: Union[int, None]):
	'''	Apply offsets and limits to the provided resource query.

		@input resources (sqlalchemy.orm.query.Query): Query to which the offset/limit
			should be applied to.
		@input offset (int or None): Offset to (optionally) apply to query results.
		@input limit (int or None): Limit to (optionally) apply to query results.

		@returns sqlalchemy.orm.query.Query
	'''
	# Both offset and limit defined
	if offset is not None and limit:
		return resources[offset:limit+offset]

	# Offset without limit
	elif offset is not None and limit is None:
		return resources[offset:]

	# Limit without offset
	elif offset:
		return resources[:limit]

	raise ValueError(
		'Invalid options for offset="%s" and limit="%s". Offset and limit must be intgers or Null values.' 
			% (offset, limit))