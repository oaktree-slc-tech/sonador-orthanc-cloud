"""study-acl-comments

Revision ID: a7c4e1f2b9d3
Revises: 3926f2350a7e
Create Date: 2026-06-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c4e1f2b9d3'
down_revision = '3926f2350a7e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sonador_auth_user_study') as batch_op:
        batch_op.add_column(sa.Column('comment_edit', sa.Boolean()))
        batch_op.add_column(sa.Column('comment_view', sa.Boolean()))

    with op.batch_alter_table('sonador_auth_group_study') as batch_op:
        batch_op.add_column(sa.Column('comment_edit', sa.Boolean()))
        batch_op.add_column(sa.Column('comment_view', sa.Boolean()))


def downgrade():
    with op.batch_alter_table('sonador_auth_group_study') as batch_op:
        batch_op.drop_column('comment_view')
        batch_op.drop_column('comment_edit')

    with op.batch_alter_table('sonador_auth_user_study') as batch_op:
        batch_op.drop_column('comment_view')
        batch_op.drop_column('comment_edit')
