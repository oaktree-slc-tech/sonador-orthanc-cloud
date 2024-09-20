"""user and group ACL models

Revision ID: cfbc7146e46f
Revises: 28913ecc1e2e
Create Date: 2024-02-29 16:18:29.523480

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cfbc7146e46f'
down_revision = '28913ecc1e2e'
branch_labels = None
depends_on = None


def upgrade():
    ''' Create ACL tables for {user,group}/{patient,study,series}

        * sonador_auth_user_patient
        * sonador_auth_user_study
        * sonador_auth_user_series
        * sonador_auth_group_patient
        * sonador_auth_group_study
        * sonador_auth_group_series
    '''
    # Create user resource tables
    op.create_table('sonador_auth_user_patient', 
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('user', sa.Integer(), nullable=False),
        sa.Column('resource', sa.String(length=64), nullable=False),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('view', sa.Boolean()),
        sa.Column('modify', sa.Boolean()),
        sa.Column('remove', sa.Boolean()),
        sa.Column('acl', sa.Boolean()),
    )
    op.create_unique_constraint(
        'uq_sonadorauth_user_patient_resource', 'sonador_auth_user_patient', ['user', 'resource'])

    op.create_table('sonador_auth_user_study', 
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('user', sa.Integer(), nullable=False),
        sa.Column('resource', sa.String(length=64), nullable=False),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('view', sa.Boolean()),
        sa.Column('modify', sa.Boolean()),
        sa.Column('remove', sa.Boolean()),
        sa.Column('acl', sa.Boolean()),
    )
    op.create_unique_constraint(
        'uq_sonadorauth_user_study_resource', 'sonador_auth_user_study', ['user', 'resource'])

    op.create_table('sonador_auth_user_series', 
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('user', sa.Integer(), nullable=False),
        sa.Column('resource', sa.String(length=64), nullable=False),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('view', sa.Boolean()),
        sa.Column('modify', sa.Boolean()),
        sa.Column('remove', sa.Boolean()),
        sa.Column('acl', sa.Boolean()),
        sa.Column('comment_edit', sa.Boolean()),
        sa.Column('comment_view', sa.Boolean()),
    )
    op.create_unique_constraint(
        'uq_sonadorauth_user_series_resource', 'sonador_auth_user_series', ['user', 'resource'])
    
    # Create group resource tables
    op.create_table('sonador_auth_group_patient', 
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('group', sa.Integer(), nullable=False),
        sa.Column('resource', sa.String(length=64), nullable=False),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('view', sa.Boolean()),
        sa.Column('modify', sa.Boolean()),
        sa.Column('remove', sa.Boolean()),
        sa.Column('acl', sa.Boolean()),
    )
    op.create_unique_constraint(
        'uq_sonadorauth_group_patient_resource', 'sonador_auth_group_patient', ['group', 'resource'])

    op.create_table('sonador_auth_group_study', 
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('group', sa.Integer(), nullable=False),
        sa.Column('resource', sa.String(length=64), nullable=False),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('view', sa.Boolean()),
        sa.Column('modify', sa.Boolean()),
        sa.Column('remove', sa.Boolean()),
        sa.Column('acl', sa.Boolean()),
    )
    op.create_unique_constraint(
        'uq_sonadorauth_group_study_resource', 'sonador_auth_group_study', ['group', 'resource'])

    op.create_table('sonador_auth_group_series', 
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('group', sa.Integer(), nullable=False),
        sa.Column('resource', sa.String(length=64), nullable=False),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('view', sa.Boolean()),
        sa.Column('modify', sa.Boolean()),
        sa.Column('remove', sa.Boolean()),
        sa.Column('acl', sa.Boolean()),
        sa.Column('comment_edit', sa.Boolean()),
        sa.Column('comment_view', sa.Boolean()),
    )
    op.create_unique_constraint(
        'uq_sonadorauth_group_series_resource', 'sonador_auth_group_series', ['group', 'resource'])


def downgrade():
    ''' Remove ACL tables
    '''
    # Drop unique constraints
    op.drop_constraint('uq_sonadorauth_user_patient_resource', 'sonador_auth_user_patient')
    op.drop_constraint('uq_sonadorauth_user_study_resource', 'sonador_auth_user_study')
    op.drop_constraint('uq_sonadorauth_user_series_resource', 'sonador_auth_user_series')
    op.drop_constraint('uq_sonadorauth_group_patient_resource', 'sonador_auth_group_patient')
    op.drop_constraint('uq_sonadorauth_group_study_resource', 'sonador_auth_group_study')
    op.drop_constraint('uq_sonadorauth_group_series_resource', 'sonador_auth_group_series')

    # Drop user resource tables
    op.drop_table('sonador_auth_user_patient')
    op.drop_table('sonador_auth_user_study')
    op.drop_table('sonador_auth_user_series')

    # Drop group resource tables
    op.drop_table('sonador_auth_group_patient')
    op.drop_table('sonador_auth_group_study')
    op.drop_table('sonador_auth_group_series')
