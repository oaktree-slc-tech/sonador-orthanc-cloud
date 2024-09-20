"""create sonador study comment table

Revision ID: 68d21e28d865
Revises: d2619343a4ec
Create Date: 2024-06-12 02:30:46.871788

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type


# revision identifiers, used by Alembic.
revision = '68d21e28d865'
down_revision = 'd2619343a4ec'
branch_labels = None
depends_on = None


def upgrade():
    ''' Create Study comment table
    '''
    # Create study comment table
    op.create_table('sonador_study_comment',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('study_id', sa.String(length=64)),
        sa.Column('user', sa.BigInteger(), nullable=True),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('text', sa.Text()),
        sa.Column('orthanc', mutable_json_type(dbtype=JSONB, nested=True)),
    )

    # Add meta/orthanc field to series comment
    with op.batch_alter_table('sonador_series_comment') as batch_op:        
        batch_op.add_column(sa.Column('orthanc', mutable_json_type(dbtype=JSONB, nested=True)))


def downgrade():

    # Drop study comment table
    op.drop_table('sonador_study_comment')

    # Drop orthanc field from comment table
    with op.batch_alter_table('sonador_series_comment') as batch_op:
        batch_op.drop_column('orthanc')