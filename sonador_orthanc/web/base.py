from abc import ABC
import asyncio, logging, json

from client.utils.decorators import classproperty
from client import apisettings as gcapicodes

from sonador.serialization import SonadorJsonEncoder
from sonador_orthanc_common.web import OrthancBaseView

logger = logging.getLogger(__name__)
