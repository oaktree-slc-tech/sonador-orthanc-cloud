"""create user preferences table

Revision ID: f9b57cef275b
Revises: ff17436f1a10
Create Date: 2025-04-17 22:33:57.688009

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f9b57cef275b'
down_revision = 'ff17436f1a10'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('sonador_user_preferences',
        sa.Column('user', sa.Integer(), nullable=True),
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('data', sa.JSON(length=64), nullable=True),
    )

def downgrade():
    op.drop_table('sonador_user_preferences')
