'''	Bootstrap for the runtime tests: tests that exercise the plugin's view classes with their
	real dependencies (SQLAlchemy models, Pydantic forms, the Sonador client) and doubles only
	for the database session, the request output and the Kafka producer.

	Unlike `tests/`, nothing here is stubbed except the `orthanc` SDK module, which exists only
	inside Orthanc's embedded interpreter. Run inside the plugin container, where the runtime is
	installed:

	    python3 -m pytest tests_runtime
'''
import os, sys, types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if REPO_ROOT not in sys.path:
	sys.path.insert(0, REPO_ROOT)

if 'orthanc' not in sys.modules:
	orthanc = types.ModuleType('orthanc')

	for name in ('LogError', 'LogWarning', 'LogInfo', 'RegisterRestCallback'):
		setattr(orthanc, name, lambda *args, **kwargs: None)

	orthanc.ChangeType = types.SimpleNamespace(
		STABLE_PATIENT=1, STABLE_STUDY=2, STABLE_SERIES=3, ORTHANC_STARTED=4, ORTHANC_STOPPED=5)
	orthanc.InstanceOrigin = types.SimpleNamespace(DICOM_PROTOCOL=1, REST_API=2)

	sys.modules['orthanc'] = orthanc


def pytest_sessionstart(session):
	'''	Import every model module up front. SQLAlchemy configures all mappers together, and the
		relationships on the cache models name classes from sibling modules by string.
	'''
	import importlib, pkgutil
	import sonador_orthanc.db as db

	for _, name, _ in pkgutil.iter_modules(db.__path__):
		importlib.import_module('sonador_orthanc.db.%s' % name)
