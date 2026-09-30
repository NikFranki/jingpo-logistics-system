"""Configure stations and routes; retain V2 history and idempotent responses.

Revision ID: a83c9e14d602
Revises: f714e269c2db
"""
import hashlib
from alembic import op
import sqlalchemy as sa

revision = "a83c9e14d602"
down_revision = "f714e269c2db"
branch_labels = None
depends_on = None


def _map_json(value, destination):
    if isinstance(value, list):
        return [_map_json(item, destination) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _map_json(item, destination) for key, item in value.items()}
    if "shipment_no" in result and "order_id" in result:
        if destination is None:
            raise RuntimeError("Station C is required for cached V2 shipment responses")
        result["destination_station_id"] = str(destination)
    if "task_no" in result and "route_code" in result:
        result["delay_monitoring_enabled"] = result["route_code"] == "AB"
    return result


def upgrade():
    conn = op.get_bind()
    destination = conn.execute(sa.text("SELECT id FROM stations WHERE code='C'")).scalar_one_or_none()
    if destination is None and conn.execute(sa.text("SELECT count(*) FROM shipments")).scalar_one():
        raise RuntimeError("Station C is required for existing V2 shipments")
    invalid = conn.execute(sa.text("""
        SELECT r.id FROM transport_routes r
        JOIN stations origin ON origin.id=r.origin_station_id
        JOIN stations target ON target.id=r.destination_station_id
        WHERE (r.code='AB' AND (origin.code<>'A' OR target.code<>'B'))
           OR (r.code='BC' AND (origin.code<>'B' OR target.code<>'C')) LIMIT 1
    """)).first()
    if invalid:
        raise RuntimeError(f"Invalid V2 route endpoints: {invalid!r}")
    for table, constraint in [("stations", "ck_stations_code"), ("transport_routes", "ck_routes_code")]:
        op.drop_constraint(constraint, table, type_="check")
        op.alter_column(table, "code", type_=sa.String(32))
        op.create_check_constraint(constraint, table, "code ~ '^[A-Z0-9][A-Z0-9_-]{0,31}$'")
        op.add_column(table, sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    for field in ("allows_first_arrival", "allows_delivery"):
        op.add_column("stations", sa.Column(field, sa.Boolean(), nullable=False, server_default=sa.text("false")))
    for table in ("transport_routes", "transport_tasks"):
        op.add_column(table, sa.Column("delay_monitoring_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    conn.execute(sa.text("UPDATE stations SET allows_first_arrival=(code='A'), allows_delivery=(code='C')"))
    conn.execute(sa.text("UPDATE transport_routes SET delay_monitoring_enabled=(code='AB')"))
    conn.execute(sa.text("UPDATE transport_tasks t SET delay_monitoring_enabled=(r.code='AB') FROM transport_routes r WHERE t.route_id=r.id"))
    op.add_column("shipments", sa.Column("destination_station_id", sa.BigInteger(), nullable=True))
    conn.execute(sa.text("UPDATE shipments SET destination_station_id=:station"), {"station": destination})
    op.alter_column("shipments", "destination_station_id", nullable=False)
    op.create_foreign_key("fk_shipments_destination_station", "shipments", "stations", ["destination_station_id"], ["id"])
    op.drop_constraint("ck_logs_resource_type", "operation_logs", type_="check")
    op.create_check_constraint("ck_logs_resource_type", "operation_logs", "resource_type IN ('ORDER', 'SHIPMENT', 'TRANSPORT_TASK', 'CLOCK', 'STATION', 'ROUTE')")
    logs = conn.execute(sa.text("SELECT id, action, resource_type, resource_id, before_data, after_data, response_body, request_hash FROM operation_logs ORDER BY id")).mappings().all()
    for log in logs:
        params = {key: _map_json(log[key], destination) for key in ("before_data", "after_data", "response_body")}
        params.update(id=log["id"], request_hash=log["request_hash"])
        if log["resource_type"] == "SHIPMENT" and log["action"] == "CREATE_SHIPMENT":
            if destination is None:
                raise RuntimeError("Station C is required for V2 creation keys")
            params["request_hash"] = hashlib.sha256(f"CREATE_SHIPMENT:{log['response_body']['order_id']}:{destination}".encode()).hexdigest()
        conn.execute(sa.text("UPDATE operation_logs SET before_data=:before_data, after_data=:after_data, response_body=:response_body, request_hash=:request_hash WHERE id=:id").bindparams(
            *(sa.bindparam(key, type_=sa.JSON()) for key in ("before_data", "after_data", "response_body"))), params)


def downgrade():
    raise RuntimeError("V3 networks cannot be downgraded losslessly; restore the pre-upgrade backup")
