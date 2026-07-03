''' Helpers for the server-owned worklist review history and reserved `Meta` keys.

	The worklist item's `orthanc` JSONB (surfaced to clients as `Meta`) carries three reserved,
	server-owned keys: `RequestedProcedure`, `PerformedProcedure`, and `ReviewHistory`. These are
	never writable through the general `Meta` channel (FR-7); this module provides the utilities the
	worklist views use to strip inbound reserved keys and to build the per-transition review history
	(FR-5). Refer to orthanc-sonador#54 section 6.1.
'''
import datetime

from ..validation.procedure import RESERVED_META_KEYS


def _now_iso():
	'''	Current UTC timestamp as an ISO-8601 string (JSON-serializable for the JSONB blob).
	'''
	return datetime.datetime.now(datetime.timezone.utc).isoformat()


def strip_reserved_keys(meta):
	'''	Return a copy of the provided `Meta`/`orthanc` mapping with all server-owned reserved
		keys removed. Protects the reserved namespace from client overwrites (FR-7).

		@input meta (dict|None)
		@returns dict
	'''
	if not meta:
		return {}
	return { k: v for k, v in dict(meta).items() if k not in RESERVED_META_KEYS }


def extract_reserved_keys(meta):
	'''	Return a copy of only the server-owned reserved keys present in the provided mapping.

		@input meta (dict|None)
		@returns dict
	'''
	if not meta:
		return {}
	return { k: dict(meta)[k] for k in RESERVED_META_KEYS if k in meta }


def build_history_entry(previous_state, state, user_pk, comment_uid=None):
	'''	Build a single `ReviewHistory` entry describing a state transition (FR-5).

		@input previous_state (str|None): prior worklist state (None on creation)
		@input state (str): new worklist state
		@input user_pk (int): actor performing the transition
		@input comment_uid (str|None): UID of the note linked to this transition (FR-4)

		@returns dict
	'''
	return {
		'PreviousState': previous_state,
		'State': state,
		'User': user_pk,
		'Timestamp': _now_iso(),
		'CommentUID': comment_uid,
	}
