"""Add line service timetables and dated scheduled trips."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "8f2c6d41a930"
down_revision = "7c91b2e4d6f8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "line_services",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("line_id", sa.BigInteger(), sa.ForeignKey("path_plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("weekdays", postgresql.JSONB(), nullable=False),
        sa.Column("timezone", sa.String(64), server_default="Asia/Shanghai", nullable=False),
        sa.Column("capacity_snapshot", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint("line_id", "code", name="uq_line_services_line_code"),
        sa.CheckConstraint("valid_until IS NULL OR valid_until >= valid_from", name="ck_line_services_valid_dates"),
        sa.CheckConstraint("jsonb_typeof(weekdays) = 'array'", name="ck_line_services_weekdays_array"),
        sa.CheckConstraint("jsonb_typeof(capacity_snapshot) = 'object'", name="ck_line_services_capacity_object"),
        sa.CheckConstraint("version > 0", name="ck_line_services_version"),
    )
    op.create_index("ix_line_services_line_enabled", "line_services", ["line_id", "enabled"])
    op.create_table(
        "line_service_stops",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("service_id", sa.BigInteger(), sa.ForeignKey("line_services.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("arrival_day_offset", sa.SmallInteger(), nullable=True),
        sa.Column("arrival_time", sa.Time(), nullable=True),
        sa.Column("departure_day_offset", sa.SmallInteger(), nullable=True),
        sa.Column("departure_time", sa.Time(), nullable=True),
        sa.UniqueConstraint("service_id", "position", name="uq_line_service_stops_position"),
        sa.CheckConstraint("position >= 0", name="ck_line_service_stops_position"),
        sa.CheckConstraint("arrival_day_offset BETWEEN 0 AND 30", name="ck_line_service_arrival_day"),
        sa.CheckConstraint("departure_day_offset BETWEEN 0 AND 30", name="ck_line_service_departure_day"),
    )
    op.create_table(
        "scheduled_trips",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("service_id", sa.BigInteger(), sa.ForeignKey("line_services.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("service_version", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.BigInteger(), sa.ForeignKey("path_plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("line_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), server_default="PLANNED", nullable=False),
        sa.Column("stops_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("capacity_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("service_id", "service_date", "service_version", name="uq_scheduled_trips_service_version_date"),
        sa.CheckConstraint("status IN ('PLANNED','CANCELLED','COMPLETED')", name="ck_scheduled_trips_status"),
        sa.CheckConstraint("jsonb_typeof(stops_snapshot) = 'array'", name="ck_scheduled_trips_stops_array"),
        sa.CheckConstraint("jsonb_typeof(capacity_snapshot) = 'object'", name="ck_scheduled_trips_capacity_object"),
    )
    op.create_index("ix_scheduled_trips_date_status", "scheduled_trips", ["service_date", "status"])
    op.add_column("transport_tasks", sa.Column("scheduled_trip_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_transport_tasks_scheduled_trip", "transport_tasks", "scheduled_trips",
                          ["scheduled_trip_id"], ["id"], ondelete="RESTRICT")
    op.add_column("shipment_schedule_versions", sa.Column("scheduled_trip_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_schedule_versions_scheduled_trip", "shipment_schedule_versions", "scheduled_trips",
                          ["scheduled_trip_id"], ["id"], ondelete="RESTRICT")
    op.drop_constraint("ck_logs_resource_type", "operation_logs", type_="check")
    op.create_check_constraint("ck_logs_resource_type", "operation_logs",
        "resource_type IN ('ORDER', 'SHIPMENT', 'TRANSPORT_TASK', 'CLOCK', 'STATION', 'ROUTE', 'PATH_PLAN', 'LINE_SERVICE', 'SCHEDULED_TRIP')")


def downgrade():
    has_new_data = op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM line_services) OR EXISTS "
        "(SELECT 1 FROM operation_logs WHERE resource_type IN ('LINE_SERVICE', 'SCHEDULED_TRIP'))"
    )).scalar()
    if has_new_data:
        raise RuntimeError("Restore a pre-migration backup; line service and scheduled trip history must be preserved.")
    op.drop_constraint("ck_logs_resource_type", "operation_logs", type_="check")
    op.create_check_constraint("ck_logs_resource_type", "operation_logs",
        "resource_type IN ('ORDER', 'SHIPMENT', 'TRANSPORT_TASK', 'CLOCK', 'STATION', 'ROUTE', 'PATH_PLAN')")
    op.drop_constraint("fk_schedule_versions_scheduled_trip", "shipment_schedule_versions", type_="foreignkey")
    op.drop_column("shipment_schedule_versions", "scheduled_trip_id")
    op.drop_constraint("fk_transport_tasks_scheduled_trip", "transport_tasks", type_="foreignkey")
    op.drop_column("transport_tasks", "scheduled_trip_id")
    op.drop_index("ix_scheduled_trips_date_status", table_name="scheduled_trips")
    op.drop_table("scheduled_trips")
    op.drop_table("line_service_stops")
    op.drop_index("ix_line_services_line_enabled", table_name="line_services")
    op.drop_table("line_services")
