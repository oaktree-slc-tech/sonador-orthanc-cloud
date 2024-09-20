"""create device table for distortion filter API

Revision ID: d0571d222908
Revises: 368291a7a036
Create Date: 2024-05-31 20:20:20.000020

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd0571d222908'
down_revision = '368291a7a036'
branch_labels = None
depends_on = None


def upgrade():
    ''' Create device table
    '''
    op.create_table('sonador_distortionfilter_devices',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('institution_name', sa.String(length=64)),
        sa.Column('manufacturer', sa.String(length=64)),
        sa.Column('manufacturer_modelname', sa.String(length=64)),
        sa.Column('software_versions', sa.String(length=64)),
        sa.Column('dcm_tag_name', sa.String(length=64)),
        sa.Column('dcm_tag_value', sa.String(length=64)),
    )


def downgrade():
    op.drop_table('sonador_distortionfilter_devices')
