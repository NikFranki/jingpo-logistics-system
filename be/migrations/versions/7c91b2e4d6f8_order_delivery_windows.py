"""Store order handover and delivery windows."""
from alembic import op
import sqlalchemy as sa

revision = "7c91b2e4d6f8"
down_revision = "d63a19e85b40"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("orders", "shipments"):
        op.add_column(table, sa.Column("earliest_handover_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("latest_delivery_at", sa.DateTime(timezone=True), nullable=True))
        op.create_check_constraint(
            f"ck_{table}_delivery_window",
            table,
            "earliest_handover_at IS NULL OR latest_delivery_at IS NULL "
            "OR earliest_handover_at <= latest_delivery_at",
        )


def downgrade():
    for table in ("shipments", "orders"):
        op.drop_constraint(f"ck_{table}_delivery_window", table, type_="check")
        op.drop_column(table, "latest_delivery_at")
        op.drop_column(table, "earliest_handover_at")
