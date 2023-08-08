import copy, re, fnmatch, logging

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_IMAGE, \
	DCMHEADER_MODALITIES_IN_STUDY

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

	dcm = { 'ID': cpatient.uid, 'MainDicomTags': dcmtags_main, 'Type': resource_type or cpatient.type }
	if cpatient.studies:
		dcm['Studies'] = cpatient.studies
	if cpatient.stable is not None:
		dcm['IsStable'] = cpatient.stable
	if cpatient.mtime:
		dcm['LastUpdate'] = cpatient.mtime

	return dcm


def cache_orthanc_studyjson(cstudy, resource_type=None):
	'''	Create Orthanc JSON structure for the provided cache study. 
	'''
	if getattr(cstudy, 'privatetags', None):
		dcmtags_main = copy.deepcopy(cstudy.orthanc)
		dcmtags_main.update(cstudy.privatetags.orthanc)
	else: dcmtags_main = cstudy.orthanc

	dcm = { 'ID': cstudy.uid, 'MainDicomTags': dcmtags_main, 'Type': resource_type or cstudy.type }
	if cstudy.parent:
		dcm['ParentPatient'] = cstudy.parent_id
		dcm['PatientMainDicomTags'] = cstudy.parent.orthanc
	if cstudy.series:
		dcm['Series'] = cstudy.series
	if cstudy.stable is not None:
		dcm['IsStable'] = cstudy.stable
	if cstudy.mtime:
		dcm['LastUpdate'] = cstudy.mtime

	# Check MainDicomTags to see if modalities in study has been populated
	if not dcm['MainDicomTags'].get(DCMHEADER_MODALITIES_IN_STUDY) and cstudy.modalities:
		dcm['MainDicomTags'][DCMHEADER_MODALITIES_IN_STUDY] = cstudy.modalities

	return dcm


def cache_orthanc_seriesjson(cseries, resource_type=None):
	'''	Crfeate Orthanc JSON structure for the provided cache series.
	'''
	if getattr(cseries, 'privatetags', None):
		dcmtags_main = copy.deepcopy(cseries.orthanc)
		dcmtags_main.update(cstudy.privatetags.orthanc)
	else: dcmtags_main = cseries.orthanc

	dcm = { 'ID': cseries.uid, 'MainDicomTags': dcmtags_main, 'Type': resource_type or cseries.type }
	if cseries.parent_id:
		dcm['ParentStudy'] = cseries.parent_id
	if cseries.instances:
		dcm['Instances'] = cseries.instances
	if cseries.stable is not None:
		dcm['IsStable'] = cseries.stable
	if cseries.mtime:
		dcm['LastUpdate'] = cseries.mtime

	return dcm
