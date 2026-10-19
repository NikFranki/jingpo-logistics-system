"""Index trip stop snapshots for origin/destination candidate filtering."""

from alembic import op

revision = "b8c4d2e19f0a"
down_revision = "f81a3d6c2907"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_scheduled_trips_stops_snapshot_gin",
        "scheduled_trips",
        ["stops_snapshot"],
        postgresql_using="gin",
        postgresql_ops={"stops_snapshot": "jsonb_path_ops"},
    )


def downgrade():
    op.drop_index("ix_scheduled_trips_stops_snapshot_gin", table_name="scheduled_trips")
