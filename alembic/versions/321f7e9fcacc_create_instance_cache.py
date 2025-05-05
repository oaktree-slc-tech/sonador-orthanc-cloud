"""create instance cache

Revision ID: 321f7e9fcacc
Revises: ff17436f1a10
Create Date: 2025-04-30 00:44:20.445907

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type


# revision identifiers, used by Alembic.
revision = '321f7e9fcacc'
down_revision = 'ff17436f1a10'
branch_labels = None
depends_on = None


def upgrade():
    ''' Create timestamp and private DICOM tags for instances
    '''
    # Create private tag model for instance
    op.create_table('sonador_cache_instance_private',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('orthanc', mutable_json_type(dbtype=JSONB, nested=True)),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('stable', sa.Boolean(), nullable=True),
    )

    # Instance Date/Time Model
    op.create_table('sonador_cache_instance_datetime',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=False),
        sa.Column('date_tag', sa.String(length=64), primary_key=True, unique=False),
        sa.Column('time_tag', sa.String(length=64), primary_key=True, unique=False),
        sa.Column('ts', sa.DateTime()),
    )
    op.create_unique_constraint(
        'uq_sonador_instance_datetime_pk', 'sonador_cache_instance_datetime', ['uid', 'date_tag', 'time_tag'])


def downgrade():
    op.drop_table('sonador_cache_instance_private')
    op.drop_table('sonador_cache_instance_datetime')
