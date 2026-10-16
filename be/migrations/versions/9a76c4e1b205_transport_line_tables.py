"""Move line definitions from legacy path-plan tables to transport-line tables."""
from alembic import op
import sqlalchemy as sa


revision = "9a76c4e1b205"
down_revision = "8f2c6d41a930"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "transport_lines",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("origin_station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("destination_station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=False),
        sa.CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9_-]{0,31}$'", name="ck_transport_lines_code"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_transport_lines_name"),
        sa.CheckConstraint("version > 0", name="ck_transport_lines_version"),
        sa.CheckConstraint("origin_station_id <> destination_station_id", name="ck_transport_lines_endpoints"),
    )
    op.create_table(
        "transport_line_legs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("line_id", sa.BigInteger(), sa.ForeignKey("transport_lines.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("route_id", sa.BigInteger(), sa.ForeignKey("transport_routes.id"), nullable=False),
        sa.Column("travel_override_minutes", sa.Integer(), nullable=True),
        sa.Column("origin_transfer_override_minutes", sa.Integer(), nullable=True),
        sa.UniqueConstraint("line_id", "position", name="uq_transport_line_legs_position"),
        sa.CheckConstraint("position >= 0", name="ck_transport_line_legs_position"),
        sa.CheckConstraint("origin_transfer_override_minutes BETWEEN 0 AND 525600", name="ck_transport_line_legs_transfer"),
        sa.CheckConstraint("travel_override_minutes BETWEEN 1 AND 525600", name="ck_transport_line_legs_travel"),
    )

    op.execute("""
        INSERT INTO transport_lines (id, code, name, enabled, version, origin_station_id, destination_station_id)
        SELECT id, code, name, enabled, version, origin_station_id, destination_station_id FROM path_plans
    """)
    op.execute("""
        INSERT INTO transport_line_legs
            (id, line_id, position, route_id, travel_override_minutes, origin_transfer_override_minutes)
        SELECT id, plan_id, position, route_id, travel_override_minutes, origin_transfer_override_minutes
        FROM path_plan_legs
    """)
    copied = op.get_bind().execute(sa.text("""
        SELECT
            (SELECT count(*) FROM path_plans) = (SELECT count(*) FROM transport_lines)
            AND (SELECT count(*) FROM path_plan_legs) = (SELECT count(*) FROM transport_line_legs)
    """)).scalar_one()
    if not copied:
        raise RuntimeError("Transport-line copy verification failed; transaction rolled back.")
    op.execute("SELECT setval(pg_get_serial_sequence('transport_lines','id'), COALESCE((SELECT max(id) FROM transport_lines), 1), EXISTS (SELECT 1 FROM transport_lines))")
    op.execute("SELECT setval(pg_get_serial_sequence('transport_line_legs','id'), COALESCE((SELECT max(id) FROM transport_line_legs), 1), EXISTS (SELECT 1 FROM transport_line_legs))")

    for table, constraint in (
        ("line_services", "line_services_line_id_fkey"),
        ("scheduled_trips", "scheduled_trips_line_id_fkey"),
        ("shipment_path_versions", "shipment_path_versions_source_plan_id_fkey"),
        ("shipment_schedule_versions", "shipment_schedule_versions_source_plan_id_fkey"),
    ):
        op.drop_constraint(constraint, table, type_="foreignkey")

    op.alter_column("shipment_path_versions", "source_plan_id", new_column_name="source_line_id")
    op.alter_column("shipment_path_versions", "source_plan_version", new_column_name="source_line_version")
    op.alter_column("shipment_schedule_versions", "source_plan_id", new_column_name="source_line_id")
    op.alter_column("shipment_schedule_versions", "source_plan_version", new_column_name="source_line_version")

    op.drop_table("path_plan_legs")
    op.drop_table("path_plans")

    op.create_foreign_key("line_services_line_id_fkey", "line_services", "transport_lines", ["line_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("scheduled_trips_line_id_fkey", "scheduled_trips", "transport_lines", ["line_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("shipment_path_versions_source_line_id_fkey", "shipment_path_versions", "transport_lines", ["source_line_id"], ["id"])
    op.create_foreign_key("shipment_schedule_versions_source_line_id_fkey", "shipment_schedule_versions", "transport_lines", ["source_line_id"], ["id"])

    op.drop_constraint("ck_logs_resource_type", "operation_logs", type_="check")
    op.create_check_constraint(
        "ck_logs_resource_type", "operation_logs",
        "resource_type IN ('ORDER', 'SHIPMENT', 'TRANSPORT_TASK', 'CLOCK', 'STATION', 'ROUTE', 'PATH_PLAN', 'TRANSPORT_LINE', 'LINE_SERVICE', 'SCHEDULED_TRIP')",
    )


def downgrade():
    op.create_table(
        "path_plans",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("origin_station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("destination_station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=False),
        sa.CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9_-]{0,31}$'", name="ck_path_plans_code"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_path_plans_name"),
        sa.CheckConstraint("version > 0", name="ck_path_plans_version"),
        sa.CheckConstraint("origin_station_id <> destination_station_id", name="ck_path_plans_endpoints"),
    )
    op.create_table(
        "path_plan_legs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("path_plans.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("route_id", sa.BigInteger(), sa.ForeignKey("transport_routes.id"), nullable=False),
        sa.Column("travel_override_minutes", sa.Integer(), nullable=True),
        sa.Column("origin_transfer_override_minutes", sa.Integer(), nullable=True),
        sa.UniqueConstraint("plan_id", "position", name="uq_path_plan_legs_position"),
        sa.CheckConstraint("position >= 0", name="ck_path_plan_legs_position"),
        sa.CheckConstraint("origin_transfer_override_minutes BETWEEN 0 AND 525600", name="ck_path_plan_legs_transfer"),
        sa.CheckConstraint("travel_override_minutes BETWEEN 1 AND 525600", name="ck_path_plan_legs_travel"),
    )
    op.execute("""
        INSERT INTO path_plans (id, code, name, enabled, version, origin_station_id, destination_station_id)
        SELECT id, code, name, enabled, version, origin_station_id, destination_station_id FROM transport_lines
    """)
    op.execute("""
        INSERT INTO path_plan_legs
            (id, plan_id, position, route_id, travel_override_minutes, origin_transfer_override_minutes)
        SELECT id, line_id, position, route_id, travel_override_minutes, origin_transfer_override_minutes
        FROM transport_line_legs
    """)
    op.execute("SELECT setval(pg_get_serial_sequence('path_plans','id'), COALESCE((SELECT max(id) FROM path_plans), 1), EXISTS (SELECT 1 FROM path_plans))")
    op.execute("SELECT setval(pg_get_serial_sequence('path_plan_legs','id'), COALESCE((SELECT max(id) FROM path_plan_legs), 1), EXISTS (SELECT 1 FROM path_plan_legs))")

    for table, constraint in (
        ("line_services", "line_services_line_id_fkey"),
        ("scheduled_trips", "scheduled_trips_line_id_fkey"),
        ("shipment_path_versions", "shipment_path_versions_source_line_id_fkey"),
        ("shipment_schedule_versions", "shipment_schedule_versions_source_line_id_fkey"),
    ):
        op.drop_constraint(constraint, table, type_="foreignkey")
    op.alter_column("shipment_path_versions", "source_line_id", new_column_name="source_plan_id")
    op.alter_column("shipment_path_versions", "source_line_version", new_column_name="source_plan_version")
    op.alter_column("shipment_schedule_versions", "source_line_id", new_column_name="source_plan_id")
    op.alter_column("shipment_schedule_versions", "source_line_version", new_column_name="source_plan_version")
    op.create_foreign_key("line_services_line_id_fkey", "line_services", "path_plans", ["line_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("scheduled_trips_line_id_fkey", "scheduled_trips", "path_plans", ["line_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("shipment_path_versions_source_plan_id_fkey", "shipment_path_versions", "path_plans", ["source_plan_id"], ["id"])
    op.create_foreign_key("shipment_schedule_versions_source_plan_id_fkey", "shipment_schedule_versions", "path_plans", ["source_plan_id"], ["id"])

    op.execute("UPDATE operation_logs SET resource_type = 'PATH_PLAN' WHERE resource_type = 'TRANSPORT_LINE'")
    op.drop_constraint("ck_logs_resource_type", "operation_logs", type_="check")
    op.create_check_constraint(
        "ck_logs_resource_type", "operation_logs",
        "resource_type IN ('ORDER', 'SHIPMENT', 'TRANSPORT_TASK', 'CLOCK', 'STATION', 'ROUTE', 'PATH_PLAN', 'LINE_SERVICE', 'SCHEDULED_TRIP')",
    )
    op.drop_table("transport_line_legs")
    op.drop_table("transport_lines")
