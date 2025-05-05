import itertools

from sonador.apisettings import IMAGING_SERVER_RESOURCE_SUPPORTED

from client.errors import ConfigurationError
from sonador.apisettings import DicomDatetimePairKey, IMAGING_SERVER_RESOURCE_PATIENT, IMAGING_SERVER_RESOURCE_STUDY, \
	IMAGING_SERVER_RESOURCE_SERIES, IMAGING_SERVER_RESOURCE_IMAGE, IMAGING_SERVER_RESOURCE_SUPPORTED
from sonador_orthanc_common.apisettings import ORTHANC_CONFIG_SECTION_DICT

from ..apisettings import ORTHANC_CONFIG_SECTION_SONADOR, SONADOR_CONF_PRIVATE_TAGS, SONADOR_CONF_DATETIME_TAGS
from ..helpers import orthanc_maindicom_tags


def orthanc_conf_privatedict(conf):
	'''	Retrieve the dictionary of private tags from the Orthanc configuration
	'''
	conf_dicom_privatedict = conf.get(ORTHANC_CONFIG_SECTION_DICT, {})
	conf_dicom_privatedict['Tags'] = set(t[1] for t in conf_dicom_privatedict.values())

	return conf_dicom_privatedict


def orthanc_conf_privatetags(conf):
	'''	Retrieve the private tags configuration for the Sonador resource cache from the config
	'''
	return conf.get(SONADOR_CONF_PRIVATE_TAGS, {})


def orthanc_conf_datetime_tags(conf):
	'''	Retrieve DICOM date/time extension tags
	'''
	conf_dcm_datetime_tags = conf.get(SONADOR_CONF_DATETIME_TAGS, {})
	conf_dcm_datetime_tags['Tags'] = {}

	return conf_dcm_datetime_tags


def check_cache_tagconfig(conf, cache_dcmtags=None, conf_dcm_privatedict=None, conf_dcm_privatetags=None,
		conf_dcm_datetime_tags=None):
	'''	Retrieve components of the Orthanc configuration and ensure that tags are properly registered.
	'''
	# Private DICOM tag structures
	conf_dcm_privatedict = conf_dcm_privatedict or orthanc_conf_privatedict(conf)
	conf_dcm_privatetags = conf_dcm_privatetags or orthanc_conf_privatetags(conf)
	conf_dcm_datetime_tags = conf_dcm_datetime_tags or orthanc_conf_datetime_tags(conf)
	cache_dcmtags = cache_dcmtags or orthanc_maindicom_tags(conf, dcm_privatetags=conf_dcm_privatetags)

	# Ensure that all private tags in "PrivateMainDicomTags" have been registered with Orthanc
	for ptag in itertools.chain(
		conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_PATIENT, []),
		conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_STUDY, []),
		conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_SERIES, []),
		conf_dcm_privatetags.get(IMAGING_SERVER_RESOURCE_IMAGE, [])):

		if not ptag in conf_dcm_privatedict['Tags']:
			raise ConfigurationError(('Invalid configuration. Private tag "%s" included in PrivateMainDicomTags which is not '
				+ 'registered in the Orthanc Dictionary. Please refer to: https://oak-tree.tech/blog/soandor-orthanc-private-headers')
		 	% ptag)

	# Ensure that all date/time tags are included in the extra main DICOM tags
	for rtype,rdatetime_tags in conf_dcm_datetime_tags.items():

		if rtype in IMAGING_SERVER_RESOURCE_SUPPORTED:

			for dtags in rdatetime_tags:
				dtags = list(dtags.split(','))

				# Only a single tag defined (assume to be a date tag), add a "blank" string for the time
				if len(dtags) == 1:
					dtags[1] = ''

				# More than two components defined. Date/time tags should be of the form: DateTag,TimeTag
				elif len(dtags) > 2:
					raise ValueError(('Invalid %s configuration "%s". Datetime tags must be date values or date/time pairs.'
						+  'Examples: "SeriesDate", "SeriesDate,SeriesTime"' ) % (SONADOR_CONF_DATETIME_TAGS, ','.join(dtags)))

				dtmeta = DicomDatetimePairKey(rtype, *tuple(dtags))
				conf_dcm_datetime_tags['Tags'][dtmeta.date_tag] = dtmeta

				# Ensure that the date tag is registered in the API response tagset
				if not dtmeta.date_tag in cache_dcmtags.get(dtmeta.resource, []):
					raise ConfigurationError('Invalid %s configuration. Tag "%s" (resource=%s) not configured for ExtraMainDicomTags.' % (
						SONADOR_CONF_DATETIME_TAGS, dtmeta.date_tag, dtmeta.resource,
					))

				if dtmeta.time_tag and not dtmeta.time_tag in cache_dcmtags.get(dtmeta.resource, []):
					raise ConfigurationError('Invalid %s configuration. Tag "%s" (resource=%s) not configured for ExtraMainDicomTags.' % (
						SONADOR_CONF_DATETIME_TAGS, dtmeta.time_tag, dtmeta.resource,
					))

	return cache_dcmtags, conf_dcm_privatedict, conf_dcm_privatetags, conf_dcm_datetime_tags