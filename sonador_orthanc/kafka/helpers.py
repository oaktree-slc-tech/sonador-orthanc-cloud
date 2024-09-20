import logging, traceback
from confluent_kafka import Producer
import orthanc

from ..apisettings import KAFKA_TIMEOUT_DEFAULT, ORTHANC_CONFIG_SECTION_SONADOR, SONADOR_CONF_KAFKA, \
	SONADOR_CONF_KAFKA_SERVERS, SONADOR_CONF_KAFKA_TOPIC, SONADOR_KAFKA_BOOTSTRAP
from ..manager import TIMER_30S, TIMER_MINUTE, TIMER_10MIN, TIMER_30MIN, TIMER_HOUR, TIMER_DAILY


def get_kafka_servers(conf_kafka):
	'''	Retrive Kafka server list from the provided configuration
	'''
	return ','.join(conf_kafka.get(SONADOR_CONF_KAFKA_SERVERS, [])) if isinstance(conf_kafka.get(SONADOR_CONF_KAFKA_SERVERS), (tuple, list)) \
		else CONF_KAFKA.get(SONADOR_CONF_KAFKA_SERVERS) if isinstance(CONF_KAFKA.get(SONADOR_CONF_KAFKA_SERVERS), six.string_types) \
		else None