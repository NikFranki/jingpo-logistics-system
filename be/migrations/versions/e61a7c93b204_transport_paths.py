"""Reusable path plans and versioned shipment paths; preserve legacy tasks."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "e61a7c93b204"
down_revision = "d92f4b76e301"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('path_plans',
        sa.Column('id',sa.BigInteger(),sa.Identity(),primary_key=True),
        sa.Column('code',sa.String(32),nullable=False,unique=True),
        sa.Column('name',sa.String(100),nullable=False),
        sa.Column('enabled',sa.Boolean(),nullable=False,server_default=sa.text('true')),
        sa.Column('version',sa.Integer(),nullable=False,server_default='1'),
        sa.Column('origin_station_id',sa.BigInteger(),sa.ForeignKey('stations.id'),nullable=False),
        sa.Column('destination_station_id',sa.BigInteger(),sa.ForeignKey('stations.id'),nullable=False),
        sa.CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9_-]{0,31}$'",name='ck_path_plans_code'),
        sa.CheckConstraint('length(btrim(name)) > 0',name='ck_path_plans_name'),
        sa.CheckConstraint('version > 0',name='ck_path_plans_version'),
        sa.CheckConstraint('origin_station_id <> destination_station_id',name='ck_path_plans_endpoints'))
    op.create_table('path_plan_legs',
        sa.Column('id',sa.BigInteger(),sa.Identity(),primary_key=True),
        sa.Column('plan_id',sa.BigInteger(),sa.ForeignKey('path_plans.id'),nullable=False),
        sa.Column('position',sa.Integer(),nullable=False),
        sa.Column('route_id',sa.BigInteger(),sa.ForeignKey('transport_routes.id'),nullable=False),
        sa.UniqueConstraint('plan_id','position',name='uq_path_plan_legs_position'),
        sa.CheckConstraint('position >= 0',name='ck_path_plan_legs_position'))
    op.create_table('shipment_path_legs',
        sa.Column('id',sa.BigInteger(),sa.Identity(),primary_key=True),
        sa.Column('shipment_id',sa.BigInteger(),sa.ForeignKey('shipments.id'),nullable=False),
        sa.Column('position',sa.Integer(),nullable=False),
        sa.Column('route_id',sa.BigInteger(),sa.ForeignKey('transport_routes.id'),nullable=False),
        sa.Column('superseded_at',sa.DateTime(timezone=True),nullable=True),
        sa.CheckConstraint('position >= 0',name='ck_shipment_path_legs_position'))
    op.create_index('uq_shipment_path_legs_current','shipment_path_legs',['shipment_id','position'],unique=True,
                    postgresql_where=sa.text('superseded_at IS NULL'))
    op.create_table('shipment_path_versions',
        sa.Column('id',sa.BigInteger(),sa.Identity(),primary_key=True),
        sa.Column('shipment_id',sa.BigInteger(),sa.ForeignKey('shipments.id'),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('destination_station_id',sa.BigInteger(),sa.ForeignKey('stations.id'),nullable=False),
        sa.Column('source_plan_id',sa.BigInteger(),sa.ForeignKey('path_plans.id'),nullable=True),
        sa.Column('source_plan_version',sa.Integer(),nullable=True),
        sa.Column('reason',sa.String(500),nullable=False),
        sa.Column('legs',JSONB(),nullable=False),
        sa.Column('occurred_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('shipment_id','version',name='uq_shipment_path_versions'),
        sa.CheckConstraint('version > 0',name='ck_shipment_path_versions_version'),
        sa.CheckConstraint("jsonb_typeof(legs) = 'array'",name='ck_shipment_path_versions_legs'),
        sa.CheckConstraint('length(btrim(reason)) BETWEEN 1 AND 500',name='ck_shipment_path_versions_reason'))
    op.add_column('shipments',sa.Column('path_version',sa.Integer(),nullable=False,server_default='0'))
    op.create_check_constraint('ck_shipments_path_version','shipments','path_version >= 0')
    op.add_column('task_shipments',sa.Column('path_leg_id',sa.BigInteger(),nullable=True))
    op.create_foreign_key('fk_task_shipments_path_leg','task_shipments','shipment_path_legs',['path_leg_id'],['id'])
    op.create_index('uq_task_shipments_active_leg','task_shipments',['path_leg_id'],unique=True,
                    postgresql_where=sa.text('released_at IS NULL AND path_leg_id IS NOT NULL'))
    op.drop_constraint('ck_logs_resource_type','operation_logs',type_='check')
    op.create_check_constraint('ck_logs_resource_type','operation_logs',
        "resource_type IN ('ORDER', 'SHIPMENT', 'TRANSPORT_TASK', 'CLOCK', 'STATION', 'ROUTE', 'PATH_PLAN')")
    # No inferred paths, no rewritten logistics facts, operation hashes or cached snapshots.


def downgrade():
    raise RuntimeError('V6 path history cannot be downgraded losslessly; restore the pre-upgrade backup')
