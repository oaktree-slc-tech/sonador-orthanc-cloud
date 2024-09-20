"""create tag table

Revision ID: ff17436f1a10
Revises: 68d21e28d865
Create Date: 2024-06-18 22:39:51.059663

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ff17436f1a10'
down_revision = '68d21e28d865'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('sonador_tag',
        sa.Column('group', sa.Integer(), nullable=True),
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('value', sa.String(length=64), nullable=True),
        sa.Column('meaning', sa.String(length=64), nullable=True),
        sa.Column('scheme_designator', sa.String(length=64), nullable=True),
        sa.Column('scheme_version', sa.String(length=64), nullable=True),
    )

def downgrade():
    op.drop_table('sonador_tag')
