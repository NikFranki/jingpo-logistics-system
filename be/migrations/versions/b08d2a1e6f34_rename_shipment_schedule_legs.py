"""Rename shipment path legs to shipment schedule legs."""

from alembic import op
import sqlalchemy as sa


revision = "b08d2a1e6f34"
down_revision = "9a76c4e1b205"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("uq_task_shipments_active_leg", table_name="task_shipments")
    op.alter_column("task_shipments", "path_leg_id", new_column_name="schedule_leg_id")
    op.rename_table("shipment_path_legs", "shipment_schedule_legs")
    op.drop_constraint("ck_shipment_path_legs_position", "shipment_schedule_legs", type_="check")
    op.create_check_constraint(
        "ck_shipment_schedule_legs_position",
        "shipment_schedule_legs",
        "position >= 0",
    )
    op.drop_index("uq_shipment_path_legs_current", table_name="shipment_schedule_legs")
    op.create_index(
        "uq_shipment_schedule_legs_current",
        "shipment_schedule_legs",
        ["shipment_id", "position"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_index(
        "uq_task_shipments_active_schedule_leg",
        "task_shipments",
        ["schedule_leg_id"],
        unique=True,
        postgresql_where=sa.text(
            "association_state IN ('PLANNED','ACTIVE') AND schedule_leg_id IS NOT NULL"
        ),
    )


def downgrade():
    op.drop_index("uq_task_shipments_active_schedule_leg", table_name="task_shipments")
    op.drop_index("uq_shipment_schedule_legs_current", table_name="shipment_schedule_legs")
    op.create_index(
        "uq_shipment_path_legs_current",
        "shipment_schedule_legs",
        ["shipment_id", "position"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.drop_constraint("ck_shipment_schedule_legs_position", "shipment_schedule_legs", type_="check")
    op.create_check_constraint(
        "ck_shipment_path_legs_position",
        "shipment_schedule_legs",
        "position >= 0",
    )
    op.rename_table("shipment_schedule_legs", "shipment_path_legs")
    op.alter_column("task_shipments", "schedule_leg_id", new_column_name="path_leg_id")
    op.create_index(
        "uq_task_shipments_active_leg",
        "task_shipments",
        ["path_leg_id"],
        unique=True,
        postgresql_where=sa.text(
            "association_state IN ('PLANNED','ACTIVE') AND path_leg_id IS NOT NULL"
        ),
    )
