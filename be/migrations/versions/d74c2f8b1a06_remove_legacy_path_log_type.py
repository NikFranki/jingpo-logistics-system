"""Remove the retired path-plan operation-log resource type."""

from alembic import op


revision = "d74c2f8b1a06"
down_revision = "c71d5f0a8b32"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.drop_constraint("ck_logs_resource_type", "operation_logs", type_="check")
        op.create_check_constraint(
            "ck_logs_resource_type",
            "operation_logs",
            "resource_type IN ('ORDER', 'SHIPMENT', 'TRANSPORT_TASK', 'CLOCK', 'STATION', 'ROUTE', 'TRANSPORT_LINE', 'LINE_SERVICE', 'SCHEDULED_TRIP')",
        )


def downgrade():
    raise RuntimeError("restore the pre-upgrade backup; path-plan logs were removed.")
