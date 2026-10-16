"""Use location-independent shipment stages and tracking events.

Revision ID: f714e269c2db
Revises: ccce65c68961

The V1 values cannot be reconstructed from the V2 stage alone. Restore a
database backup for a full rollback; downgrade deliberately refuses to guess.
"""

from __future__ import annotations

import hashlib
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f714e269c2db"
down_revision: Union[str, Sequence[str], None] = "ccce65c68961"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STAGES = {
    "AT_A": "AT_STATION",
    "AT_B": "AT_STATION",
    "AT_C": "AT_STATION",
    "IN_TRANSIT_AB": "IN_TRANSIT",
    "IN_TRANSIT_BC": "IN_TRANSIT",
}
EVENTS = {
    "ENTER_A": "ARRIVE",
    "ARRIVE_B": "ARRIVE",
    "ARRIVE_C": "ARRIVE",
    "DEPART_AB": "DEPART",
    "DEPART_BC": "DEPART",
}
EVENT_ACTIONS = {
    "PICKUP_SHIPMENT": "PICKUP",
    "ENTER_STATION": "ARRIVE",
    "START_DELIVERY": "START_DELIVERY",
    "SIGN_SHIPMENT": "SIGN",
}


def _map_json(value):
    if isinstance(value, dict):
        mapped = {}
        for key, item in value.items():
            if key == "stage" and isinstance(item, str):
                mapped[key] = STAGES.get(item, item)
            elif key == "event_type" and isinstance(item, str):
                mapped[key] = EVENTS.get(item, item)
            elif key == "shipment_stages" and isinstance(item, dict):
                mapped[key] = {shipment_id: STAGES.get(stage, stage) for shipment_id, stage in item.items()}
            elif key == "allowed_actions" and isinstance(item, list):
                mapped[key] = []
                for action in item:
                    normalized = _map_json(action)
                    if isinstance(normalized, dict):
                        if normalized.get("action") == "ENTER_A":
                            normalized["action"] = "ARRIVE"
                        reasons = {
                            "Entering station A requires PICKED_UP stage": "Entering the first station requires PICKED_UP stage",
                            "Delivery requires AT_C stage": "Delivery requires arrival at station C",
                        }
                        reason = normalized.get("reason")
                        if isinstance(reason, str):
                            normalized["reason"] = reasons.get(reason, reason)
                    mapped[key].append(normalized)
            else:
                mapped[key] = _map_json(item)
        if "tracking_events" in mapped and "stage" in mapped:
            mapped.setdefault("active_transport_task", None)
        return mapped
    if isinstance(value, list):
        return [_map_json(item) for item in value]
    return value


def _validate_history(conn) -> None:
    bad_shipments = conn.execute(sa.text("""
        SELECT s.id, s.stage, st.code
        FROM shipments s
        LEFT JOIN stations st ON st.id = s.last_scanned_station_id
        WHERE (s.stage = 'AT_A' AND st.code IS DISTINCT FROM 'A')
           OR (s.stage = 'AT_B' AND st.code IS DISTINCT FROM 'B')
           OR (s.stage = 'AT_C' AND st.code IS DISTINCT FROM 'C')
           OR (s.stage = 'IN_TRANSIT_AB' AND st.code IS DISTINCT FROM 'A')
           OR (s.stage = 'IN_TRANSIT_BC' AND st.code IS DISTINCT FROM 'B')
           OR (s.stage IN ('OUT_FOR_DELIVERY', 'SIGNED') AND st.code IS DISTINCT FROM 'C')
        LIMIT 1
    """)).first()
    if bad_shipments is not None:
        raise RuntimeError(f"Invalid V1 shipment location: {bad_shipments!r}")

    bad_events = conn.execute(sa.text("""
        SELECT e.id, e.event_type, st.code AS station_code, r.code AS route_code
        FROM tracking_events e
        LEFT JOIN stations st ON st.id = e.station_id
        LEFT JOIN transport_tasks t ON t.id = e.task_id
        LEFT JOIN transport_routes r ON r.id = t.route_id
        WHERE (e.event_type = 'ENTER_A' AND st.code IS DISTINCT FROM 'A')
           OR (e.event_type = 'DEPART_AB' AND (st.code IS DISTINCT FROM 'A' OR r.code IS DISTINCT FROM 'AB'))
           OR (e.event_type = 'ARRIVE_B' AND (st.code IS DISTINCT FROM 'B' OR r.code IS DISTINCT FROM 'AB'))
           OR (e.event_type = 'DEPART_BC' AND (st.code IS DISTINCT FROM 'B' OR r.code IS DISTINCT FROM 'BC'))
           OR (e.event_type = 'ARRIVE_C' AND (st.code IS DISTINCT FROM 'C' OR r.code IS DISTINCT FROM 'BC'))
        LIMIT 1
    """)).first()
    if bad_events is not None:
        raise RuntimeError(f"Invalid V1 tracking event: {bad_events!r}")

    bad_transit = conn.execute(sa.text("""
        SELECT s.id, s.stage
        FROM shipments s
        LEFT JOIN task_shipments ts ON ts.shipment_id = s.id AND ts.released_at IS NULL
        LEFT JOIN transport_tasks t ON t.id = ts.task_id
        LEFT JOIN transport_routes r ON r.id = t.route_id
        WHERE s.stage IN ('IN_TRANSIT_AB', 'IN_TRANSIT_BC')
          AND (t.status IS DISTINCT FROM 'IN_TRANSIT'
               OR r.code IS DISTINCT FROM CASE WHEN s.stage = 'IN_TRANSIT_AB' THEN 'AB' ELSE 'BC' END)
        LIMIT 1
    """)).first()
    if bad_transit is not None:
        raise RuntimeError(f"Invalid V1 active transport: {bad_transit!r}")

    bad_pending = conn.execute(sa.text("""
        SELECT s.id, s.stage, t.status, origin.code
        FROM task_shipments ts
        JOIN shipments s ON s.id = ts.shipment_id
        JOIN transport_tasks t ON t.id = ts.task_id
        JOIN transport_routes r ON r.id = t.route_id
        JOIN stations origin ON origin.id = r.origin_station_id
        WHERE ts.released_at IS NULL
          AND t.status = 'PENDING_DEPARTURE'
          AND (s.stage NOT IN ('AT_A', 'AT_B')
               OR s.last_scanned_station_id IS DISTINCT FROM origin.id)
        LIMIT 1
    """)).first()
    if bad_pending is not None:
        raise RuntimeError(f"Invalid V1 pending transport: {bad_pending!r}")


def upgrade() -> None:
    conn = op.get_bind()
    _validate_history(conn)

    op.drop_constraint("ck_shipments_stage", "shipments", type_="check")
    op.drop_constraint("ck_events_type", "tracking_events", type_="check")
    op.drop_constraint("ck_events_task", "tracking_events", type_="check")
    op.drop_constraint("ck_events_station", "tracking_events", type_="check")

    conn.execute(sa.text("""
        UPDATE shipments SET stage = CASE stage
            WHEN 'AT_A' THEN 'AT_STATION'
            WHEN 'AT_B' THEN 'AT_STATION'
            WHEN 'AT_C' THEN 'AT_STATION'
            WHEN 'IN_TRANSIT_AB' THEN 'IN_TRANSIT'
            WHEN 'IN_TRANSIT_BC' THEN 'IN_TRANSIT'
            ELSE stage END
    """))
    conn.execute(sa.text("""
        UPDATE tracking_events SET event_type = CASE event_type
            WHEN 'ENTER_A' THEN 'ARRIVE'
            WHEN 'ARRIVE_B' THEN 'ARRIVE'
            WHEN 'ARRIVE_C' THEN 'ARRIVE'
            WHEN 'DEPART_AB' THEN 'DEPART'
            WHEN 'DEPART_BC' THEN 'DEPART'
            ELSE event_type END
    """))

    op.create_check_constraint(
        "ck_shipments_stage", "shipments",
        "stage IN ('PENDING_PICKUP', 'PICKED_UP', 'AT_STATION', 'IN_TRANSIT', 'OUT_FOR_DELIVERY', 'SIGNED')",
    )
    op.create_check_constraint(
        "ck_events_type", "tracking_events",
        "event_type IN ('SHIPMENT_CREATED', 'PICKUP', 'ARRIVE', 'DEPART', 'START_DELIVERY', 'SIGN')",
    )
    op.create_check_constraint(
        "ck_events_task", "tracking_events",
        "(event_type = 'DEPART' AND task_id IS NOT NULL) OR "
        "(event_type = 'ARRIVE') OR "
        "(event_type IN ('SHIPMENT_CREATED', 'PICKUP', 'START_DELIVERY', 'SIGN') AND task_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_events_station", "tracking_events",
        "(event_type IN ('ARRIVE', 'DEPART') AND station_id IS NOT NULL) OR "
        "(event_type IN ('SHIPMENT_CREATED', 'PICKUP', 'START_DELIVERY', 'SIGN') AND station_id IS NULL)",
    )

    a_station_id = conn.execute(sa.text("SELECT id FROM stations WHERE code = 'A'")).scalar_one_or_none()
    logs = conn.execute(sa.text("""
        SELECT id, action, resource_type, resource_id, before_data, after_data, response_body
        FROM operation_logs ORDER BY id
    """)).mappings().all()
    for log in logs:
        changes = {
            column: _map_json(log[column])
            for column in ("before_data", "after_data", "response_body")
        }
        params = {"id": log["id"]}
        params.update(changes)
        if log["resource_type"] == "SHIPMENT" and log["action"] in EVENT_ACTIONS:
            event_type = EVENT_ACTIONS[log["action"]]
            if event_type == "ARRIVE" and a_station_id is None:
                raise RuntimeError("Station A is required to migrate ENTER_A idempotency keys")
            station = str(a_station_id) if event_type == "ARRIVE" else ""
            content = f"SHIPMENT_EVENT:{log['resource_id']}:{event_type}:{station}"
            params["request_hash"] = hashlib.sha256(content.encode()).hexdigest()
            conn.execute(sa.text("""
                UPDATE operation_logs SET before_data = :before_data, after_data = :after_data,
                    response_body = :response_body, request_hash = :request_hash WHERE id = :id
            """).bindparams(
                sa.bindparam("before_data", type_=sa.JSON()),
                sa.bindparam("after_data", type_=sa.JSON()),
                sa.bindparam("response_body", type_=sa.JSON()),
            ), params)
        else:
            conn.execute(sa.text("""
                UPDATE operation_logs SET before_data = :before_data, after_data = :after_data,
                    response_body = :response_body WHERE id = :id
            """).bindparams(
                sa.bindparam("before_data", type_=sa.JSON()),
                sa.bindparam("after_data", type_=sa.JSON()),
                sa.bindparam("response_body", type_=sa.JSON()),
            ), params)


def downgrade() -> None:
    raise RuntimeError("V2 history cannot be downgraded losslessly; restore the pre-upgrade backup")
