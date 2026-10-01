"""Add trigram index for contains-search on synthetic vehicle identifiers."""

from alembic import op

revision = "0003_vehicle_search"
down_revision = "0002_alert_dedupe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX IF NOT EXISTS ix_vehicles_vehicle_id_trgm ON vehicles USING gin (vehicle_id gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_vehicles_vehicle_id_trgm")
