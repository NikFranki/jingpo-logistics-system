"""Store user-bound Agent action proposals and business audit correlations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'a11c0de20210'
down_revision = 'b8c4d2e19f0a'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('operation_logs', sa.Column('agent_action_proposal_id', postgresql.UUID(as_uuid=True)))
    op.add_column('operation_logs', sa.Column('agent_candidate_id', postgresql.UUID(as_uuid=True)))
    op.add_column('operation_logs', sa.Column('agent_session_id', postgresql.UUID(as_uuid=True)))
    op.add_column('operation_logs', sa.Column('agent_message_id', postgresql.UUID(as_uuid=True)))
    op.create_index('ix_operation_logs_agent_proposal', 'operation_logs', ['agent_action_proposal_id'])
    op.create_table(
        'auth_agent_write_policies',
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('auth_users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('enabled_action_types', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("jsonb_typeof(enabled_action_types) = 'array'", name='ck_agent_write_policy_actions_array'),
    )
    op.create_table(
        'agent_action_proposals',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('actor_user_id', sa.BigInteger(), sa.ForeignKey('auth_users.id'), nullable=False),
        sa.Column('actor_auth_version', sa.Integer(), nullable=False),
        sa.Column('policy_version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('actor_session_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('auth_sessions.public_id'), nullable=False),
        sa.Column('candidate_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_session_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('message_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('action_type', sa.String(64), nullable=False),
        sa.Column('subject_type', sa.String(32), nullable=False),
        sa.Column('subject_id', sa.BigInteger(), nullable=False),
        sa.Column('payload', postgresql.JSONB(), nullable=False),
        sa.Column('payload_hash', sa.String(64), nullable=False),
        sa.Column('snapshot_version', sa.DateTime(timezone=True), nullable=False),
        sa.Column('preview', postgresql.JSONB(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='PREVIEWED'),
        sa.Column('approval_mode', sa.String(16)),
        sa.Column('result', postgresql.JSONB()),
        sa.Column('operation_log_id', sa.BigInteger(), sa.ForeignKey('operation_logs.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('candidate_id', name='uq_agent_action_proposals_candidate'),
        sa.CheckConstraint("action_type IN ('order.update','shipment.destination.update')", name='ck_agent_action_proposals_action'),
        sa.CheckConstraint("status IN ('PREVIEWED','SUCCEEDED','REJECTED','EXPIRED','FAILED')", name='ck_agent_action_proposals_status'),
        sa.CheckConstraint('subject_id > 0', name='ck_agent_action_proposals_subject_id'),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name='ck_agent_action_proposals_payload_hash'),
    )
    op.create_index('ix_agent_action_proposals_owner_status', 'agent_action_proposals', ['actor_user_id', 'status'])
    op.create_index('ix_agent_action_proposals_expires_at', 'agent_action_proposals', ['expires_at'])


def downgrade():
    op.drop_index('ix_agent_action_proposals_expires_at', table_name='agent_action_proposals')
    op.drop_index('ix_agent_action_proposals_owner_status', table_name='agent_action_proposals')
    op.drop_table('agent_action_proposals')
    op.drop_table('auth_agent_write_policies')
    op.drop_index('ix_operation_logs_agent_proposal', table_name='operation_logs')
    op.drop_column('operation_logs', 'agent_message_id')
    op.drop_column('operation_logs', 'agent_session_id')
    op.drop_column('operation_logs', 'agent_candidate_id')
    op.drop_column('operation_logs', 'agent_action_proposal_id')
