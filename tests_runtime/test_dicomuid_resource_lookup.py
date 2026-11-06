"""	DICOM UID lookup: the identifier table holds the UIDs of every level, so a study UID which a
	series also carries as its SeriesInstanceUID must still resolve to the study when the study
	is requested, and to the series when the series is requested.
"""
import pytest

sqlalchemy = pytest.importorskip('sqlalchemy')

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import UnmappedClassError

from sonador_orthanc.db.base import AutoDbBase
from sonador_orthanc.db.internal import Resource, DicomIdentifiers, \
	ORTHANCDB_PATIENT_TYPE, ORTHANCDB_STUDY_TYPE, ORTHANCDB_SERIES_TYPE
from sonador_orthanc.db.helpers import dcmuid_fetch_dicomidentifier_model


SHARED_UID = '1.2.826.0.1.3680043.8.498.10000000000000000001'
STUDY_ID = '040c7831-3c20153d-8c6a8798-aa891dd9-2656f28e'
SERIES_ID = '2e48f2ff-b86c5dfc-59c1feee-70290331-ed9e6bd7'

# The Orthanc index tables mapped by sonador_orthanc.db.internal, reduced to the columns used here
ORTHANC_SCHEMA = (
	'CREATE TABLE changes (seq BIGINT PRIMARY KEY, changetype INTEGER, internalid BIGINT, '
		'resourcetype INTEGER, date VARCHAR(64))',
	'CREATE TABLE resources (internalid BIGINT PRIMARY KEY, resourcetype INTEGER NOT NULL, '
		'publicid VARCHAR(64) NOT NULL, parentid BIGINT REFERENCES resources(internalid))',
	'CREATE TABLE dicomidentifiers (id BIGINT NOT NULL REFERENCES resources(internalid), '
		'taggroup INTEGER NOT NULL, tagelement INTEGER NOT NULL, value TEXT, '
		'PRIMARY KEY (id, taggroup, tagelement))',
)


@pytest.fixture(scope='module')
def session():
	engine = create_engine('sqlite://')
	with engine.begin() as conn:
		for statement in ORTHANC_SCHEMA:
			conn.execute(text(statement))

		# Patient > study > series, where the series reuses the study UID as its SeriesInstanceUID.
		# Rows are inserted series-first so that an unordered lookup would find the series.
		conn.execute(text('INSERT INTO resources VALUES (1, :t, :p, NULL)'),
			{ 't': ORTHANCDB_PATIENT_TYPE, 'p': 'patient' })
		conn.execute(text('INSERT INTO resources VALUES (2, :t, :p, 1)'), { 't': ORTHANCDB_STUDY_TYPE, 'p': STUDY_ID })
		conn.execute(text('INSERT INTO resources VALUES (3, :t, :p, 2)'), { 't': ORTHANCDB_SERIES_TYPE, 'p': SERIES_ID })
		conn.execute(text('INSERT INTO dicomidentifiers VALUES (3, 32, 14, :v)'), { 'v': SHARED_UID })
		conn.execute(text('INSERT INTO dicomidentifiers VALUES (2, 32, 13, :v)'), { 'v': SHARED_UID })

	# The Orthanc models are reflected from the index database when the plugin starts
	try:
		inspect(DicomIdentifiers)
	except (NoInspectionAvailable, UnmappedClassError):
		AutoDbBase.prepare(autoload_with=engine)

	with sessionmaker(bind=engine)() as s:
		yield s


def test_study_level_lookup_returns_the_study(session):
	di = dcmuid_fetch_dicomidentifier_model(session, SHARED_UID, resource_type=ORTHANCDB_STUDY_TYPE)

	assert di.resource.publicid == STUDY_ID
	assert di.resource.resourcetype == ORTHANCDB_STUDY_TYPE


def test_series_level_lookup_returns_the_series(session):
	di = dcmuid_fetch_dicomidentifier_model(session, SHARED_UID, resource_type=ORTHANCDB_SERIES_TYPE)

	assert di.resource.publicid == SERIES_ID
	assert di.resource.resourcetype == ORTHANCDB_SERIES_TYPE


def test_lookup_at_a_level_without_the_uid_returns_nothing(session):
	assert dcmuid_fetch_dicomidentifier_model(session, SHARED_UID, resource_type=ORTHANCDB_PATIENT_TYPE) is None


def test_lookup_without_a_level_still_resolves_a_resource(session):
	di = dcmuid_fetch_dicomidentifier_model(session, SHARED_UID)

	assert di.resource.publicid in (STUDY_ID, SERIES_ID)
