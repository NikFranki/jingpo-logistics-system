"""Cancel pending transport plans without losing history or replay caches."""
from alembic import op
import sqlalchemy as sa

revision = "d92f4b76e301"
down_revision = "a83c9e14d602"
branch_labels = None
depends_on = None

STATUS_TIMES = (
    "((status = 'PENDING_DEPARTURE' AND departed_at IS NULL AND arrived_at IS NULL) "
    "OR (status = 'IN_TRANSIT' AND departed_at IS NOT NULL AND arrived_at IS NULL) "
    "OR (status = 'ARRIVED' AND departed_at IS NOT NULL AND arrived_at IS NOT NULL "
    "AND arrived_at >= departed_at)) AND cancelled_at IS NULL AND cancel_reason IS NULL "
    "OR (status = 'CANCELLED' AND departed_at IS NULL AND arrived_at IS NULL "
    "AND cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL "
    "AND length(btrim(cancel_reason)) BETWEEN 1 AND 500)"
)


def _map_json(value):
    if isinstance(value, list):
        return [_map_json(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _map_json(item) for key, item in value.items()}
    if "task_no" in result and "route_code" in result:
        result.setdefault("cancelled_at", None)
        result.setdefault("cancel_reason", None)
        if "allowed_actions" in result:
            enabled = result.get("status") == "PENDING_DEPARTURE"
            if not any(a.get("action") == "CANCEL" for a in result["allowed_actions"]):
                result["allowed_actions"].append({"action": "CANCEL", "enabled": enabled,
                    "reason_code": None if enabled else "INVALID_STATUS",
                    "reason": None if enabled else "Cancellation requires PENDING_DEPARTURE status"})
    if "tracking_events" in result and "stage" in result and "allowed_actions" in result:
        # Historical network/occupancy qualification cannot be reconstructed from current data.
        if not any(a.get("action") == "CREATE_TRANSPORT_TASK" for a in result["allowed_actions"]):
            result["allowed_actions"].append({"action": "CREATE_TRANSPORT_TASK", "enabled": False,
                "reason_code": "REFRESH_REQUIRED", "reason": "Refresh shipment detail for current transport eligibility"})
    return result


def upgrade():
    op.add_column("transport_tasks", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("transport_tasks", sa.Column("cancel_reason", sa.String(500), nullable=True))
    op.drop_constraint("ck_tasks_status_times", "transport_tasks", type_="check")
    op.create_check_constraint("ck_tasks_status_times", "transport_tasks", STATUS_TIMES)
    conn = op.get_bind()
    logs = conn.execute(sa.text("SELECT id,before_data,after_data,response_body FROM operation_logs ORDER BY id")).mappings()
    for log in logs:
        mapped = {key: _map_json(log[key]) for key in ("before_data", "after_data", "response_body")}
        values = {key: value for key, value in mapped.items() if value != log[key]}
        if not values:
            continue
        # Do not rewrite unchanged audit values, including SQL NULL versus JSON null.
        fields = list(values)
        values["id"] = log["id"]
        assignments = ",".join(f"{key}=:{key}" for key in fields)
        conn.execute(sa.text(f"UPDATE operation_logs SET {assignments} WHERE id=:id")
            .bindparams(*(sa.bindparam(key, type_=sa.JSON()) for key in fields)), values)


def downgrade():
    raise RuntimeError("Cancelled tasks cannot be restored to V3 losslessly; restore the pre-upgrade backup")
