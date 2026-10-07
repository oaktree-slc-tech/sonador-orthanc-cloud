"""display-attributes

Revision ID: b3d9e4f1c2a7
Revises: a7c4e1f2b9d3
Create Date: 2026-10-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3d9e4f1c2a7'
down_revision = 'a7c4e1f2b9d3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('sonador_display_attribute',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('group', sa.BigInteger(), nullable=False),
        sa.Column('ctime', sa.DateTime(), nullable=True),
        sa.Column('mtime', sa.DateTime(), nullable=True),
        sa.Column('code', sa.String(length=9), nullable=False),
        sa.Column('keyword', sa.String(length=128), nullable=True),
        sa.Column('label', sa.String(length=256), nullable=True),
        sa.Column('private', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint('group', 'code', name='uq_sonador_display_attribute_group_code'),
    )


def downgrade():
    op.drop_table('sonador_display_attribute')
