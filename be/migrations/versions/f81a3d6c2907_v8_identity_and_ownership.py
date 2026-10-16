"""V8 account identity, revocable sessions, order ownership and cancellation."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f81a3d6c2907'
down_revision = 'e2a7c4b19d63'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('auth_users',
        sa.Column('id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('username', sa.String(40), nullable=False, unique=True),
        sa.Column('email', sa.String(254), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(256)),
        sa.Column('role', sa.String(16), nullable=False, server_default='user'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('needs_username', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('auth_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('admin','ops','developer','user')", name='ck_auth_users_role'),
        sa.CheckConstraint("role != 'admin' OR enabled", name='ck_auth_admin_enabled'))
    op.create_index('uq_auth_single_admin', 'auth_users', ['role'], unique=True, postgresql_where=sa.text("role = 'admin'"))
    op.create_table('auth_sessions',
        sa.Column('token_hash', sa.String(64), primary_key=True),
        sa.Column('public_id', postgresql.UUID(as_uuid=True), nullable=False, unique=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('auth_users.id'), nullable=False),
        sa.Column('channel', sa.String(16), nullable=False),
        sa.Column('parent_hash', sa.String(64), sa.ForeignKey('auth_sessions.token_hash')),
        sa.Column('csrf_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True)))
    op.create_index('ix_auth_sessions_user_id', 'auth_sessions', ['user_id'])
    op.create_index('ix_auth_sessions_expires_at', 'auth_sessions', ['expires_at'])
    op.create_table('auth_email_tokens',
        sa.Column('token_hash', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('auth_users.id'), nullable=False),
        sa.Column('purpose', sa.String(16), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True)))
    op.create_index('ix_auth_email_tokens_user_id', 'auth_email_tokens', ['user_id'])
    op.create_table('auth_google_identities',
        sa.Column('subject', sa.String(255), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('auth_users.id'), nullable=False, unique=True))
    op.create_table('auth_oauth_attempts',
        sa.Column('state_hash', sa.String(64), primary_key=True),
        sa.Column('nonce', sa.String(128), nullable=False),
        sa.Column('verifier', sa.String(128), nullable=False),
        sa.Column('session_hash', sa.String(64), sa.ForeignKey('auth_sessions.token_hash')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True)))
    op.create_table('auth_device_grants',
        sa.Column('device_hash', sa.String(64), primary_key=True),
        sa.Column('user_code_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('auth_users.id')),
        sa.Column('auth_version', sa.Integer()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_polled_at', sa.DateTime(timezone=True)),
        sa.Column('consumed_at', sa.DateTime(timezone=True)),
        sa.Column('denied', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.create_table('auth_audit',
        sa.Column('id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('actor_user_id', sa.BigInteger(), sa.ForeignKey('auth_users.id')),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('auth_users.id'), nullable=False),
        sa.Column('action', sa.String(64), nullable=False),
        sa.Column('changes', postgresql.JSONB(), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table('auth_rate_buckets',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_auth_rate_buckets_expires_at', 'auth_rate_buckets', ['expires_at'])
    op.add_column('orders', sa.Column('created_by_user_id', sa.BigInteger(), sa.ForeignKey('auth_users.id')))
    op.create_index('ix_orders_created_by_user_id', 'orders', ['created_by_user_id'])
    op.add_column('orders', sa.Column('cancelled_at', sa.DateTime(timezone=True)))
    op.add_column('orders', sa.Column('cancel_reason', sa.String(500)))
    op.add_column('operation_logs', sa.Column('actor_user_id', sa.BigInteger(), sa.ForeignKey('auth_users.id')))
    op.drop_constraint('ck_orders_status', 'orders')
    op.create_check_constraint('ck_orders_status', 'orders', "status IN ('PENDING_SHIPMENT','SHIPMENT_CREATED','COMPLETED','CANCELLED')")
    op.drop_constraint('ck_shipments_stage', 'shipments')
    op.create_check_constraint('ck_shipments_stage', 'shipments', "stage IN ('PENDING_PICKUP','PICKED_UP','AT_STATION','IN_TRANSIT','OUT_FOR_DELIVERY','SIGNED','CANCELLED')")
    op.drop_constraint('ck_shipments_scanned_station', 'shipments')
    op.create_check_constraint('ck_shipments_scanned_station', 'shipments', "(stage IN ('PENDING_PICKUP','PICKED_UP','CANCELLED') AND last_scanned_station_id IS NULL) OR (stage NOT IN ('PENDING_PICKUP','PICKED_UP','CANCELLED') AND last_scanned_station_id IS NOT NULL)")


def downgrade():
    raise RuntimeError('restore the pre-V8 backup; ownership and authentication audit must be preserved')
