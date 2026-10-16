"""Rename legacy path-plan keys in shipment operation snapshots."""

from alembic import op
import sqlalchemy as sa


revision = "e2a7c4b19d63"
down_revision = "d74c2f8b1a06"
branch_labels = None
depends_on = None


def upgrade():
    for column in ("response_body", "before_data", "after_data"):
        op.execute(sa.text(f"""
            UPDATE operation_logs
            SET {column} =
                ({column} - 'source_plan_id' - 'source_plan_version')
                || CASE WHEN {column} ? 'source_plan_id'
                    THEN jsonb_build_object('line_id', {column}->'source_plan_id')
                    ELSE '{{}}'::jsonb END
                || CASE WHEN {column} ? 'source_plan_version'
                    THEN jsonb_build_object('line_version', {column}->'source_plan_version')
                    ELSE '{{}}'::jsonb END
            WHERE jsonb_typeof({column}) = 'object'
              AND ({column} ? 'source_plan_id' OR {column} ? 'source_plan_version')
        """))


def downgrade():
    raise RuntimeError("restore the pre-upgrade backup; legacy history keys were normalized.")
