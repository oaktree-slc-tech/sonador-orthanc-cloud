"""create timestamp models

Revision ID: 81f57d8f5447
Revises: 94d46202b28b
Create Date: 2023-08-08 22:22:14.886169

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '81f57d8f5447'
down_revision = '94d46202b28b'
branch_labels = None
depends_on = None


def upgrade():
    ''' Create timetsamp models for patient, study, and series
    '''
    # Patient Date/Time
    op.create_table('sonador_cache_patient_datetime',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=False),
        sa.Column('date_tag', sa.String(length=64), primary_key=True, unique=False),
        sa.Column('time_tag', sa.String(length=64), primary_key=True, unique=False),
        sa.Column('ts', sa.DateTime()),
    )
    op.create_unique_constraint(
        'uq_sonador_patient_datetime_pk', 'sonador_cache_patient_datetime', ['uid', 'date_tag', 'time_tag'])

    # Study Date/Time
    op.create_table('sonador_cache_study_datetime',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=False),
        sa.Column('date_tag', sa.String(length=64), primary_key=True, unique=False),
        sa.Column('time_tag', sa.String(length=64), primary_key=True, unique=False),
        sa.Column('ts', sa.DateTime()),
    )
    op.create_unique_constraint(
        'uq_sonador_study_datetime_pk', 'sonador_cache_study_datetime', ['uid', 'date_tag', 'time_tag'])

    # Series Date/Time
    op.create_table('sonador_cache_series_datetime',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=False),
        sa.Column('date_tag', sa.String(length=64), primary_key=True, unique=False),
        sa.Column('time_tag', sa.String(length=64), primary_key=True, unique=False),
        sa.Column('ts', sa.DateTime()),
    )
    op.create_unique_constraint(
        'uq_sonador_series_datetime_pk', 'sonador_cache_series_datetime', ['uid', 'date_tag', 'time_tag'])


def downgrade():
    op.drop_table('sonador_cache_patient_datetime')
    op.drop_table('sonador_cache_study_datetime')
    op.drop_table('sonador_cache_series_datetime')
