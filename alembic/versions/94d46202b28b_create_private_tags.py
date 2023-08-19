"""create private tags

Revision ID: 94d46202b28b
Revises: d3845146fd7f
Create Date: 2023-07-27 19:07:24.390873

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type


# revision identifiers, used by Alembic.
revision = '94d46202b28b'
down_revision = 'd3845146fd7f'
branch_labels = None
depends_on = None


def upgrade():
    ''' Create private tag models for patient, study, and series
    '''
    op.create_table('sonador_cache_patient_private',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('orthanc', mutable_json_type(dbtype=JSONB, nested=True)),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('stable', sa.Boolean(), nullable=True),
    )
    op.create_table('sonador_cache_study_private',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('orthanc', mutable_json_type(dbtype=JSONB, nested=True)),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('stable', sa.Boolean(), nullable=True),
    )
    op.create_table('sonador_cache_series_private',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('orthanc', mutable_json_type(dbtype=JSONB, nested=True)),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('stable', sa.Boolean(), nullable=True),
    )


def downgrade():
    op.drop_table('sonador_cache_patient_private')
    op.drop_table('sonador_cache_study_private')
    op.drop_table('sonador_cache_series_private')
