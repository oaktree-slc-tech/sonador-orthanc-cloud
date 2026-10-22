"""tag-meta

Revision ID: c5e8a2d4f6b1
Revises: b3d9e4f1c2a7
Create Date: 2026-10-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = 'c5e8a2d4f6b1'
down_revision = 'b3d9e4f1c2a7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('sonador_tag', sa.Column('meta', JSONB(), nullable=True))


def downgrade():
    op.drop_column('sonador_tag', 'meta')
