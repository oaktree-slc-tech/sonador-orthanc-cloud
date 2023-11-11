#!/usr/bin/python3
import os, sys, json, logging, subprocess

from sonador import apisettings as sonadorapi
from sonador_orthanc_common import apisettings as orthancapi_common

from cli import orthanc2cli_env, SONADOR_CLI_BINARY_DEFAULT

logger = logging.getLogger(__name__)


if __name__ == '__main__':

	# Set Sonador environment configuration from Orthanc configuration
	ORTHANC_CONFIG_PATH = os.environ.get('ORTHANC_CONFIG_LINUX', orthancapi_common.ORTHANC_CONFIG_DEFAULT_LINUX)
	SONADOR_CLI_BINARY = os.environ.get('SONADOR_CLI_BINARY', SONADOR_CLI_BINARY_DEFAULT)

	if not os.path.exists(SONADOR_CLI_BINARY):
		raise ValueError('Unable to run server status script, Sonador CLI does not exist: %s' % SONADOR_CLI_BINARY)

	with orthanc2cli_env(ORTHANC_CONFIG_PATH) as ORTHANC_CONF_SONADOR:
		_status = subprocess.run(['python3', SONADOR_CLI_BINARY, 'pacs', 'status'], capture_output=True)

	if _status.stdout: logger.warning(_status.stdout.decode('utf-8'))
	if _status.stderr: logger.warning(_status.stderr.decode('utf-8'))

	sys.exit(_status.returncode)