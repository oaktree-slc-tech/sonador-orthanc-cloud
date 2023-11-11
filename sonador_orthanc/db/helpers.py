import copy, re, fnmatch, logging

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_IMAGE, \
	DCMHEADER_MODALITIES_IN_STUDY, DCM_QUERY_NULL, DCM_QUERY_NOT_NULL, \
	IMAGING_SERVER_MAINDICOM, IMAGING_SERVER_PATIENT_MAINDICOM, IMAGING_SERVER_LAST_UPDATE, IMAGING_SERVER_STABLE, \
	IMAGING_SERVER_PARENT_PATIENT, IMAGING_SERVER_PARENT_STUDY

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


def orthanc_commentjson(comment):
	'''	Create Orthanc JSONn structure for the provided comment.
	'''
	return {
		'ID': comment.uid,
		IMAGING_SERVER_RESOURCE_SERIES: comment.series_id,
		'Created': comment.ctime,
		IMAGING_SERVER_LAST_UPDATE: comment.mtime,
		'Text': comment.text,
	}
