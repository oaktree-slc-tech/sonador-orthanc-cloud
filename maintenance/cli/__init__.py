import os, sys, logging, json, contextlib
from collections import OrderedDict

from sonador import apisettings as sonadorapi
from sonador_orthanc_common import apisettings as orthancapi_common

logger = logging.getLogger(__name__)


SONADOR_CLI_BINARY_DEFAULT = os.path.join('opt', 'sonador-cli', 'sonador-cli.py')


ORTHANC2CLI_ENV_MAPPING = OrderedDict((
	(orthancapi_common.ORTHANC_SONADOR_CONFIG_URL, sonadorapi.SONADOR_URL),
	(orthancapi_common.ORTHANC_SONADOR_CONFIG_APITOKEN, sonadorapi.SONADOR_APITOKEN),
	(orthancapi_common.ORTHANC_SONADOR_CONFIG_ACCESSID, sonadorapi.SONADOR_ACCESS_ID),
	(orthancapi_common.ORTHANC_SONADOR_CONFIG_SECRET, sonadorapi.SONADOR_SECRET_KEY),
	(orthancapi_common.ORTHANC_SONADOR_CONFIG_VERIFYSSL, sonadorapi.SONADOR_VERIFY_SSL),
	(orthancapi_common.ORTHANC_SONADOR_CONFIG_INTERNALDNS, sonadorapi.SONADOR_INTERNAL_DNS),
	(orthancapi_common.ORTHANC_SERVER_ID, sonadorapi.SONADOR_IMAGING_SERVER),
))


@contextlib.contextmanager
def orthanc2cli_env(orthanc_config_path, env_mappings=ORTHANC2CLI_ENV_MAPPING):
	'''	CONTEXT MANAGER: Map Orthanc configuration parameters to CLI environment variables for running
		maintenance and status commands.

		@input orthanc_config_path (str): path to the Orthanc config

		The environment variables are unampped when the context is closed.
	'''
	orthanc_conf_sonador = {}
	if not os.path.exists(orthanc_config_path):
		raise ValueError('Unable to retrieve Orthanc configuration, config "%s" does not exist.' % orthanc_config_path)
	
	# Config folder: iterate through each file looking for the Sonador config
	if os.path.isdir(orthanc_config_path):

		for fname in os.listdir(orthanc_config_path):
			cfpath = os.path.join(orthanc_config_path, fname)
		
			with open(cfpath) as f:

				# Sanitize and load JSON data
				cjson = json.load(f)            
			
				if cjson.get(orthancapi_common.ORTHANC_CONFIG_SECTION_SONADOR):
					orthanc_conf_sonador = cjson.get(orthancapi_common.ORTHANC_CONFIG_SECTION_SONADOR)
					break

	# Map Sonador connection and configuration parameters to CLI env variables
	for conf,env in env_mappings.items():
		if orthanc_conf_sonador.get(conf):
			os.environ[env] = str(orthanc_conf_sonador.get(conf))

	# Run operation with configured environment
	try:
		yield orthanc_conf_sonador

	# Sanitize environment
	finally:
		for env in env_mappings.values():
			os.environ.pop(env, None)