"""Remove the mutable simulation clock; retain historical business timestamps."""
from alembic import op

revision = "c84e2b19a607"
down_revision = "30ec3dbeb8ac"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table("simulation_settings")


def downgrade():
    raise RuntimeError("Server-time actions cannot be restored to simulation time; restore the pre-upgrade backup")
