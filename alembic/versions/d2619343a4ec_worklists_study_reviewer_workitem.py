"""study worklists

Revision ID: d2619343a4ec
Revises: d0571d222908
Create Date: 2024-05-09 19:37:03.612704

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type


# revision identifiers, used by Alembic.
revision = 'd2619343a4ec'
down_revision = 'd0571d222908'
branch_labels = None
depends_on = None


def upgrade():

    # Create worklist/reviewer table for study
    op.create_table('sonador_worklist_reviewer_study_workitem',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('group', sa.BigInteger(), nullable=False),
        sa.Column('user', sa.BigInteger(), nullable=False),
        sa.Column('resource', sa.String(length=64), nullable=False),
        sa.Column('state', sa.String(length=512), nullable=False),
        sa.Column('orthanc', mutable_json_type(dbtype=JSONB, nested=True)),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('complete', sa.DateTime(), nullable=True),
    )


def downgrade():
    
    # Remove worklist/reviewer table 
    op.drop_table('sonador_worklist_reviewer_study_workitem')