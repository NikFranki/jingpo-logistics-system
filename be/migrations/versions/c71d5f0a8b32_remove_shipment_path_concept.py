"""Remove obsolete shipment path metadata; retain transport schedule history."""

from alembic import op
import sqlalchemy as sa


revision = "c71d5f0a8b32"
down_revision = "b08d2a1e6f34"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        UPDATE operation_logs
        SET parent_operation_id = NULL
        WHERE parent_operation_id IN (
            SELECT id FROM operation_logs WHERE resource_type = 'PATH_PLAN'
        )
    """))
    op.execute(sa.text("DELETE FROM operation_logs WHERE resource_type = 'PATH_PLAN'"))
    for column in ("response_body", "before_data", "after_data"):
        op.execute(sa.text(f"""
            UPDATE operation_logs
            SET {column} = {column} - ARRAY['path_version', 'transport_path', 'path_history', 'path_leg_id']
            WHERE jsonb_typeof({column}) = 'object'
        """))
    op.drop_table("shipment_path_versions")
    op.drop_constraint("ck_shipments_path_version", "shipments", type_="check")
    op.drop_constraint("ck_shipments_schedule_mode", "shipments", type_="check")
    op.drop_column("shipments", "path_version")
    op.drop_column("shipments", "scheduling_mode")
    op.drop_column("shipment_schedule_versions", "path_version")
    op.execute(sa.text("""
        UPDATE shipment_schedule_versions AS versions
        SET legs = migrated.legs
        FROM (
            SELECT id, jsonb_agg(
                CASE WHEN leg ? 'path_leg_id'
                    THEN (leg - 'path_leg_id') || jsonb_build_object('schedule_leg_id', leg->'path_leg_id')
                    ELSE leg
                END ORDER BY ordinal
            ) AS legs
            FROM shipment_schedule_versions
            CROSS JOIN LATERAL jsonb_array_elements(legs) WITH ORDINALITY AS items(leg, ordinal)
            GROUP BY id
        ) AS migrated
        WHERE versions.id = migrated.id
    """))


def downgrade():
    raise RuntimeError("restore the pre-upgrade backup; obsolete path history was removed.")
