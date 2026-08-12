'''	Test bootstrap for the Sonador/Orthanc plugin.

	The plugin normally runs inside Orthanc's embedded Python interpreter, where the
	`orthanc` SDK module exists and the Sonador IO client and its imaging stack (pydicom,
	highdicom, ...) are installed. Neither is true of a bare checkout, and neither is
	needed to exercise the Kafka producer configuration: `sonador_orthanc.kafka.helpers`
	deals only in dicts, strings and files.

	Rather than require the full runtime to run a unit test, the modules the import chain
	reaches but the unit under test does not use are replaced with stubs whose attributes
	resolve to their own names. Strings behave well enough for the module-level constant
	assembly in `apisettings` (joins, tuples, dict keys) that the real constants defined
	there -- the ones under test -- load unchanged.

	`confluent_kafka` is the exception: it is stubbed with a recording `Producer` so that a
	test can assert on the exact property dict the plugin hands to librdkafka, which is the
	thing that actually determines transport security.

	Run with:  python3 -m pytest tests
'''
import importlib, importlib.abc, importlib.machinery, os, sys, types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Top-level packages replaced wholesale. These are the Orthanc SDK and the Sonador imaging
# stack; nothing here participates in Kafka configuration.
#
# `sonador`, `client` and `sonador_orthanc_common` are sibling clones that sit beside the
# plugin in a working tree but are **gitignored**, so they are absent from a fresh checkout
# entirely. They are stubbed unconditionally rather than only when missing, so that the suite
# behaves the same in a developer's tree as in a clean clone.
STUB_ROOTS = ('orthanc', 'sonador', 'sonador_orthanc_common', 'client', 'highdicom', 'pydicom',
	'six')


class StubValue(str):
	'''	Stand-in for anything reached through a stubbed module.

		It is a `str` so that the module-level constant assembly in `apisettings` -- joins,
		tuple membership, dict keys, %-formatting -- behaves; and any attribute of it, or
		call on it, yields another StubValue, so that chains like
		`client.apisettings.VALIDATION_APICODE_DUPLICATE` and constructor calls like
		`DicomDatetimePairKey(...)` resolve rather than raising.
	'''
	def __getattr__(self, name):
		if name.startswith('__') and name.endswith('__'):
			raise AttributeError(name)

		return StubValue(name)

	def __call__(self, *args, **kwargs):
		return StubValue(str(self))


class StubModule(types.ModuleType):
	'''	A module whose every attribute resolves to a StubValue named for the attribute.

		`__all__` is empty so that `from <stub> import *` contributes nothing, leaving the
		real constants in the importing module untouched.
	'''
	__all__ = []
	__path__ = []

	def __getattr__(self, name):
		if name.startswith('__') and name.endswith('__'):
			raise AttributeError(name)

		return StubValue(name)


class StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
	'''	Meta-path hook that serves StubModule for STUB_ROOTS and their submodules.

		It is installed at the front of `sys.meta_path` because several of these packages
		exist as real directories in the checkout (as git submodules) and would otherwise
		be found and imported, pulling in the imaging stack this bootstrap exists to avoid.
	'''
	def find_spec(self, fullname, path=None, target=None):
		if fullname.split('.')[0] not in STUB_ROOTS:
			return None

		return importlib.machinery.ModuleSpec(fullname, self, is_package=True)

	def create_module(self, spec):
		return StubModule(spec.name)

	def exec_module(self, module):
		pass


class RecordingProducer:
	'''	Stand-in for `confluent_kafka.Producer` that records the configuration it was
		constructed with, so a test can assert on the exact librdkafka property dict.
	'''
	instances = []

	def __init__(self, config):
		self.config = dict(config)
		RecordingProducer.instances.append(self)

	def produce(self, *args, **kwargs):
		pass

	def poll(self, *args, **kwargs):
		return 0

	def flush(self, *args, **kwargs):
		return 0


def _install_stubs():
	if REPO_ROOT not in sys.path:
		sys.path.insert(0, REPO_ROOT)

	if not any(isinstance(finder, StubFinder) for finder in sys.meta_path):
		sys.meta_path.insert(0, StubFinder())

	if 'confluent_kafka' not in sys.modules:
		confluent_kafka = types.ModuleType('confluent_kafka')
		confluent_kafka.Producer = RecordingProducer
		sys.modules['confluent_kafka'] = confluent_kafka


def load_kafka_module(name):
	'''	Import a module from `sonador_orthanc/kafka/` without executing the package's
		`__init__`.

		That `__init__` is the plugin's Kafka wiring: it registers REST callbacks and change
		callbacks and reaches the Orthanc server manager and the database layer. None of it
		is involved in building a producer configuration, and stubbing far enough to import
		it would leave the test asserting against stubs rather than against the plugin.

		A synthetic `sonador_orthanc.kafka` package is registered with its `__path__` set to
		the real directory, so `helpers` and `base` load from source, with their relative
		`..apisettings` imports resolving to the real constants.

		@input name (str): submodule name, e.g. "helpers"

		@returns module
	'''
	package = 'sonador_orthanc.kafka'

	if package not in sys.modules:
		parent = importlib.import_module('sonador_orthanc')

		stub_package = types.ModuleType(package)
		stub_package.__path__ = [os.path.join(REPO_ROOT, 'sonador_orthanc', 'kafka')]
		stub_package.__package__ = package

		sys.modules[package] = stub_package
		setattr(parent, 'kafka', stub_package)

	return importlib.import_module('%s.%s' % (package, name))


_install_stubs()
