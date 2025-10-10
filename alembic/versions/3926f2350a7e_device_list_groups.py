"""device-list-groups

Revision ID: 3926f2350a7e
Revises: 321f7e9fcacc
Create Date: 2025-09-20 11:40:16.594893

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3926f2350a7e'
down_revision = '321f7e9fcacc'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sonador_distortionfilter_devices') as batch_op:
        batch_op.add_column(sa.Column('group', sa.BigInteger(), nullable=False))


def downgrade():
    with op.batch_alter_table('sonador_distortionfilter_devices') as batch_op:
        batch_op.drop_column('group')
