"""added sonador_worklist_procedurestep table

Revision ID: d3845146fd7f
Revises: 
Create Date: 2022-11-05 22:48:59.208542

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type


# revision identifiers, used by Alembic.
revision = 'd3845146fd7f'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    ''' Create worklist procedure step table
    '''
    op.create_table('sonador_worklist_procedurestep',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('orthanc', mutable_json_type(dbtype=JSONB, nested=True)),
        sa.Column('patient', mutable_json_type(dbtype=JSONB, nested=True)),
        sa.Column('request', mutable_json_type(dbtype=JSONB, nested=True,)),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('modality', sa.String(length=64)),
        sa.Column('stable', sa.Boolean(), nullable=True),
        sa.Column('priority', sa.String(256), nullable=True),
        sa.Column('scheduled', sa.DateTime(), nullable=True),
        sa.Column('started', sa.DateTime(), nullable=True),
        sa.Column('complete', sa.DateTime(), nullable=True),
        sa.Column('state', sa.String(length=64), nullable=True),
        sa.Column('patient_id', sa.String(length=64), nullable=True),
        sa.Column('study_id', sa.String(length=64), nullable=True),
        sa.Column('series_id', sa.String(length=64), nullable=True),
        sa.Column('procedure_codes', ARRAY(sa.String(), as_tuple=False, dimensions=None, zero_indexes=False), nullable=True),
    )


def downgrade():
    ''' Drop worklist procedure step
    '''
    op.drop_table('sonador_worklist_procedurestep')
