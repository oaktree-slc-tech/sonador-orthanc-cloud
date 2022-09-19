import copy, re, fnmatch

from sonador.apisettings import IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_IMAGE, \
	DCMHEADER_MODALITIES_IN_STUDY



# Regular expression transforms needed to translate DICOM query syntax to a regular expression
# compatible with PostgreSQL JSONB fields. Refer to:
# https://www.postgresql.org/docs/9.3/functions-matching.html#POSIX-EMBEDDED-OPTIONS-TABLE.
PSQL_REGEX_TRANSFORMS = (
	(r'(?<!\\)\(', r'\('), # Escape parentheses
	(r'(?<!\\)\)', r'\)'), 
	(r'(?<!\\)\*', '%*'), # Replace '*' (wildcard), with '%*' (PostgreSQL operator for any character)
)


def dcmquery2psqlregex(dcmquery, regex_transforms=PSQL_REGEX_TRANSFORMS):
	'''	Convert the provided DICOM query string to a PSQL regular expression.

		@input dcmquery (str): DICOM query string on which the the transform chain should be executed.

		@returns str: PSQL regular expression string
	'''
	# Transform DICOM query to Python regular expression
	psql_pattern = copy.copy(dcmquery)

	# Convert Python regular expression to PostgreSQL re syntax
	for p,r in regex_transforms:
		psql_pattern = re.sub(p, r, psql_pattern)

	return psql_pattern



def cache_orthanc_patientjson(cpatient, resource_type=IMAGING_SERVER_RESOURCE_PATIENT):
	'''	Create Orthanc JSON structure for the provided cache patient
	'''
	dcm = { 'ID': cpatient.uid, 'MainDicomTags': cpatient.orthanc, 'Type': resource_type }
	if cpatient.studies:
		dcm['Studies'] = cpatient.studies
	if cpatient.stable is not None:
		dcm['IsStable'] = cpatient.stable
	if cpatient.mtime:
		dcm['LastUpdate'] = cpatient.mtime

	return dcm


def cache_orthanc_studyjson(cstudy, resource_type=IMAGING_SERVER_RESOURCE_STUDY):
	'''	Create Orthanc JSON structure for the provided cache study. 
	'''
	dcm = { 'ID': cstudy.uid, 'MainDicomTags': cstudy.orthanc, 'Type': resource_type }
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


def cache_orthanc_seriesjson(cseries, resource_type=IMAGING_SERVER_RESOURCE_SERIES):
	'''	Crfeate Orthanc JSON structure for the provided cache series.
	'''
	dcm = { 'ID': cseries.uid, 'MainDicomTags': cseries.orthanc, 'Type': resource_type }
	if cseries.parent_id:
		dcm['ParentStudy'] = cseries.parent_id
	if cseries.instances:
		dcm['Instances'] = cseries.instances
	if cseries.stable is not None:
		dcm['IsStable'] = cseries.stable
	if cseries.mtime:
		dcm['LastUpdate'] = cseries.mtime

	return dcm
