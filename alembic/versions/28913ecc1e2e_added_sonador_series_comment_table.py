"""added sonador_series_comment table

Revision ID: 28913ecc1e2e
Revises: 81f57d8f5447
Create Date: 2023-09-21 12:35:24.548375

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '28913ecc1e2e'
down_revision = '81f57d8f5447'
branch_labels = None
depends_on = None


def upgrade():
    ''' Create Series comment table
    '''
    op.create_table('sonador_series_comment',
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('series_id', sa.String(length=64)),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('text', sa.Text()),
    )


def downgrade():
    op.drop_table('sonador_series_comment')