import copy, re, fnmatch, logging, datetime

from client.utils.object import pick

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_IMAGE, IMAGING_SERVER_MODIFIED, IMAGING_SERVER_LAST_UPDATE,  \
	DCMHEADER_INSTITUTION_NAME, DCMHEADER_MANUFACTURER, DCMHEADER_MANUFACTER_MODEL_NAME, DCMHEADER_SOFTWARE_VERSIONS, \
	DCMHEADER_MODALITIES_IN_STUDY, DCM_QUERY_NULL, DCM_QUERY_NOT_NULL, \
	IMAGING_SERVER_MAINDICOM, IMAGING_SERVER_PATIENT_MAINDICOM, IMAGING_SERVER_STABLE, \
	IMAGING_SERVER_PARENT_PATIENT, IMAGING_SERVER_PARENT_STUDY, IMAGING_SERVER_DCM_TAG, IMAGING_SERVER_DCM_TAG_VALUE

from .. import apisettings as sonador_api

logger = logging.getLogger(__name__)



# Regular expression transforms needed to translate DICOM query syntax to a regular expression
# compatible with PostgreSQL JSONB fields. Refer to:
# https://www.postgresql.org/docs/9.3/functions-matching.html#POSIX-EMBEDDED-OPTIONS-TABLE.
PSQL_REGEX_TRANSFORMS = (
	(r'(?<!\\)\(', r'\('), # Escape parentheses
	(r'(?<!\\)\)', r'\)'),
	(r'(?<!\\)\*', '%*'), # Replace '*' (wildcard), with '%*' (PostgreSQL operator for any character)
)


def dcmquery_psqlregex_flags(**kwargs):
	'''	Create a "flags" string to pass to PSQL via regex_match.

		@returns str or None: PSQL flags to pass to psql_match or None if no flags are defined
	'''
	flags = kwargs.get('flags', '')

	# Case sensitive
	if kwargs.get('case_sensitive') and 'i' in flags:
		flags = flags.replace('i', '')
	else: flags += 'i'

	# Return None if not flags defined
	return flags if flags else None


def dcmquery2psqlregex(dcmquery, regex_transforms=PSQL_REGEX_TRANSFORMS):
	'''	Convert the provided DICOM query string to a PSQL regular expression.

		@input dcmquery (str): DICOM query string on which the the transform chain should be executed.

		@returns str: PSQL regular expression string
	'''
	# Return NULL or NOT NULL unmodified
	if dcmquery in (DCM_QUERY_NULL, DCM_QUERY_NOT_NULL):
		return dcmquery

	# Transform DICOM query to Python regular expression
	psql_pattern = copy.copy(dcmquery)
	if not isinstance(psql_pattern, (str, bytes)):
		psql_pattern = str(psql_pattern)

	# Convert Python regular expression to PostgreSQL re syntax
	for p,r in regex_transforms:
		psql_pattern = re.sub(p, r, psql_pattern)

	return psql_pattern


def cache_orthanc_patientjson(cpatient, resource_type=None):
	'''	Create Orthanc JSON structure for the provided cache patient
	'''
	if getattr(cpatient, 'privatetags', None):
		dcmtags_main = copy.deepcopy(cpatient.orthanc)
		dcmtags_main.update(cpatient.privatetags.orthanc)
	else: dcmtags_main = cpatient.orthanc

	dcm = { 'ID': cpatient.uid, IMAGING_SERVER_MAINDICOM: dcmtags_main, 'Type': resource_type or cpatient.type }
	if cpatient.studies:
		dcm['Studies'] = cpatient.studies
	if cpatient.stable is not None:
		dcm[IMAGING_SERVER_STABLE] = cpatient.stable
	if cpatient.mtime:
		dcm[IMAGING_SERVER_LAST_UPDATE] = cpatient.mtime

	return dcm


def cache_orthanc_studyjson(cstudy, resource_type=None):
	'''	Create Orthanc JSON structure for the provided cache study.
	'''
	if getattr(cstudy, 'privatetags', None):
		dcmtags_main = copy.deepcopy(cstudy.orthanc)
		dcmtags_main.update(cstudy.privatetags.orthanc)
	else: dcmtags_main = cstudy.orthanc

	dcm = { 'ID': cstudy.uid, IMAGING_SERVER_MAINDICOM: dcmtags_main, 'Type': resource_type or cstudy.type }
	if cstudy.parent:
		dcm[IMAGING_SERVER_PARENT_PATIENT] = cstudy.parent_id
		dcm[IMAGING_SERVER_PATIENT_MAINDICOM] = cstudy.parent.orthanc
	if cstudy.series:
		dcm[IMAGING_SERVER_RESOURCE_SERIES] = cstudy.series
	if cstudy.stable is not None:
		dcm[IMAGING_SERVER_STABLE] = cstudy.stable
	if cstudy.mtime:
		dcm[IMAGING_SERVER_LAST_UPDATE] = cstudy.mtime

	# Check MainDicomTags to see if modalities in study has been populated
	if not dcm[IMAGING_SERVER_MAINDICOM].get(DCMHEADER_MODALITIES_IN_STUDY) and cstudy.modalities:
		dcm[IMAGING_SERVER_MAINDICOM][DCMHEADER_MODALITIES_IN_STUDY] = cstudy.modalities

	return dcm


def cache_orthanc_seriesjson(cseries, resource_type=None):
	'''	Crfeate Orthanc JSON structure for the provided cache series.
	'''
	if getattr(cseries, 'privatetags', None):
		dcmtags_main = copy.deepcopy(cseries.orthanc)
		dcmtags_main.update(cseries.privatetags.orthanc)
	else: dcmtags_main = cseries.orthanc

	dcm = { 'ID': cseries.uid, IMAGING_SERVER_MAINDICOM: dcmtags_main, 'Type': resource_type or cseries.type }
	if cseries.parent_id:
		dcm[IMAGING_SERVER_PARENT_STUDY] = cseries.parent_id
	if cseries.instances:
		dcm['Instances'] = cseries.instances
	if cseries.stable is not None:
		dcm[IMAGING_SERVER_STABLE] = cseries.stable
	if cseries.mtime:
		dcm[IMAGING_SERVER_LAST_UPDATE] = cseries.mtime

	return dcm


def orthanc_commentjson(comment, user=None, user_attrs=sonador_api.SONADOR_USER_ATTRS_DEFAULT):
	'''	Create Orthanc JSONn structure for the provided comment.
	'''
	_json = {
		'ID': comment.uid,
		comment.resource_cachemodel.type: getattr(comment, comment.resource_foreignkey_attr),
		'Created': comment.ctime,
		IMAGING_SERVER_LAST_UPDATE: comment.mtime,
		'Text': comment.text,
	}
	if user:
		_json['User'] = pick(user, user_attrs)
	if comment.orthanc:
		_json['Meta'] = comment.orthanc

	return _json
	

def orthanc_devicejson(device):
	'''	Create Orthanc JSON structure for the provided device.
	'''
	return {
		'ID': device.uid,
		'Created': device.ctime,
		IMAGING_SERVER_MODIFIED: device.mtime,
		DCMHEADER_INSTITUTION_NAME: device.institution_name,
		DCMHEADER_MANUFACTURER: device.manufacturer,
		DCMHEADER_MANUFACTER_MODEL_NAME: device.manufacturer_modelname,
		DCMHEADER_SOFTWARE_VERSIONS: device.software_versions,
		IMAGING_SERVER_DCM_TAG: device.dcm_tag_name,
		IMAGING_SERVER_DCM_TAG_VALUE: device.dcm_tag_value,
	}
 
def orthanc_user_preferences(pref):
	'''	Create Orthanc JSON structure for the user preferneces.
	'''
	return {
		'ID': pref.uid,
		'Data': pref.data,
		'User': pref.user,
	}

def orthanc_tagjson(tag, group=None, group_attrs=sonador_api.SONADOR_GROUP_ATTRS_DEFAULT):
	'''	Create Orthanc JSON structure for tags.
	'''
	_json = {
		'ID': tag.uid,
		'Value': tag.value,
		'SchemeDesignator': tag.scheme_designator,
		'Meaning': tag.meaning,
	}
	
	if tag.scheme_version:
		_json['SchemeVersion'] = tag.scheme_version
	if group:
		_json['Group'] = pick(group, group_attrs)
  
	return _json


def orthanc_worklist_studyjson(worklist, user=None, user_attrs=sonador_api.SONADOR_USER_ATTRS_DEFAULT, 
		group=None, group_attrs=sonador_api.SONADOR_GROUP_ATTRS_DEFAULT):
	'''	Create Orthanc JSONn structure for the provided worklist assigned to study.
	'''
	_json = {
		'ID': worklist.uid,
		IMAGING_SERVER_RESOURCE_STUDY: worklist.resource,
		'Created': worklist.ctime,
		IMAGING_SERVER_LAST_UPDATE: worklist.mtime,
		'State': worklist.state,
		'Group': worklist.group,
		'User': worklist.user
	}

	# Add metadata properties
	if worklist.orthanc:
		_json['Meta'] = worklist.orthanc

	# Add user and group attributes to response
	if user:
		_json['User'] = pick(user, user_attrs)
	if group:
		_json['Group'] = pick(group, group_attrs)

	return _json


def orthanc_auth_resourcejson(auth, attrs=sonador_api.SONADOR_ACL_ATTRS_DEFAULT, 
		user=None, user_attrs=sonador_api.SONADOR_USER_ATTRS_DEFAULT,
		group=None, group_attrs=sonador_api.SONADOR_GROUP_ATTRS_DEFAULT):
	'''	Create Orthanc JSON structure for the provided resources auth permissions for Users/Groups.
		Returns appropriate json based on input.
	'''
	# Retrieve attributes from model
	_adata = pick(auth, attrs)
	_adata['ID'] = auth.uid
	_adata[auth.resource_cachemodel.type] = auth.resource

	# Create Orthanc style API keys
	for k in attrs:
		v = _adata.pop(k, None)

		# Key transforms
		if 'acl' in k: k = k.upper()
		elif '_' in k: k = ''.join(s.title() for s in k.split('_'))
		else: k = k.title()

		if v is not None:
			_adata[k] = v

	if user:
		_adata['User'] = pick(user, user_attrs)
	elif group:
		_adata['Group'] = pick(group, group_attrs)

	# Timestamps
	_adata['Created'] = auth.ctime
	_adata[IMAGING_SERVER_LAST_UPDATE] = auth.mtime

	return _adata


def set_ctime(mapper, connection, target):
	'''	Set creation time for the provided model instance
	'''
	target.ctime = target.mtime = datetime.datetime.utcnow()


def set_mtime(mapper, connection, target):
	'''	Set the modification time for the provied model instance
	'''
	target.mtime = datetime.datetime.utcnow()
	