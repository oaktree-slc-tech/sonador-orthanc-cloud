from __future__ import with_statement

import os, json, sys, re

from alembic import context
from sqlalchemy import engine_from_config, pool
from logging.config import fileConfig

# Ensure that the root directory of the application is part of the path
ALEMBIC_ROOT = os.path.dirname(os.path.abspath(__file__))
ORTHANC_PLUGIN_ROOT = os.path.dirname(ALEMBIC_ROOT)

if not ALEMBIC_ROOT in sys.path:
    sys.path.append(ALEMBIC_ROOT)
if not ORTHANC_PLUGIN_ROOT in sys.path:
    sys.path.append(ORTHANC_PLUGIN_ROOT)


from sonador_orthanc_common.apisettings import ORTHANC_CONFIG_DEFAULT_LINUX, ORTHANC_CONFIG_SECTION_POSTGRES
from sonador_orthanc.helpers import init_postgresdb_connstr


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)


# Set database option from Orthanc config
ORTHANC_CONFIG_PATH = os.environ.get('ORTHANC_CONFIG_LINUX', ORTHANC_CONFIG_DEFAULT_LINUX)
if not os.path.exists(ORTHANC_CONFIG_PATH):
    raise ValueError('Unable to retrieve Orthanc configuration, config "%s" does not exist.' % ORTHANC_CONFIG_PATH)

ORTHANC_CONF_POSTGRESQL = {}

# Config folder: iterate through each file looking for the PostgreSQL configuration
if os.path.isdir(ORTHANC_CONFIG_PATH):

    for fname in os.listdir(ORTHANC_CONFIG_PATH):
        cfpath = os.path.join(ORTHANC_CONFIG_PATH, fname)
        
        with open(cfpath) as f:

            # Sanitize and load JSON data
            cjson = json.load(f)            
            
            if cjson.get(ORTHANC_CONFIG_SECTION_POSTGRES):
                ORTHANC_CONF_POSTGRESQL = cjson.get(ORTHANC_CONFIG_SECTION_POSTGRES)
                break

# Config file: open and attempt to read PostgreSQL section
elif os.path.isfile(ORTHANC_CONFIG_PATH):
    with open(ORTHANC_CONFIG_PATH) as f:
        cjson = json.load(f)
        ORTHANC_CONF_POSTGRESQL = cjson.get(ORTHANC_CONFIG_SECTION_POSTGRES)

if not ORTHANC_CONF_POSTGRESQL:
    raise ValueError('Unable to run Alembic operation, invalid Orthanc PostgreSQL configuration.')

# Create SQLAlchemy string from PostgreSQL file: interpolate '%' to '%%' to provide interoperability with Alembic.
# Refer to https://stackoverflow.com/questions/39849641/in-flask-migrate-valueerror-invalid-interpolation-syntax-in-connection-string-a
SQL_CONNSTR = init_postgresdb_connstr(ORTHANC_CONF_POSTGRESQL)
config.set_main_option('sqlalchemy.url', SQL_CONNSTR.replace('%', '%%'))


# Sonador/Orthanc Alembic Managed Models
from sonador_orthanc.db.base import DbBase
from sonador_orthanc.db.worklist import ProcedureStep

# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = DbBase.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
