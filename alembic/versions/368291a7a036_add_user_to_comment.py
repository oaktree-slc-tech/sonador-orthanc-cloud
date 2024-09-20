"""add user to comment

Revision ID: 368291a7a036
Revises: cfbc7146e46f
Create Date: 2024-05-31 18:15:15.014217

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '368291a7a036'
down_revision = 'cfbc7146e46f'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sonador_series_comment') as batch_op:        
        batch_op.add_column(sa.Column('user', sa.BigInteger(), nullable=True))


def downgrade():
    with op.batch_alter_table('sonador_series_comment') as batch_op:
        batch_op.drop_column('user')
