"""Add stable alert deduplication key for at-least-once event processing."""

import sqlalchemy as sa
from alembic import op

revision = "0002_alert_dedupe"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("alerts")}
    if "dedupe_key" not in columns:
        op.add_column("alerts", sa.Column("dedupe_key", sa.String(length=160), nullable=True))
        op.execute("UPDATE alerts SET dedupe_key = 'legacy:' || id::text WHERE dedupe_key IS NULL")
        op.alter_column("alerts", "dedupe_key", nullable=False)
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("alerts")}
    if "ix_alerts_dedupe_key" not in indexes:
        op.create_index("ix_alerts_dedupe_key", "alerts", ["dedupe_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_alerts_dedupe_key", table_name="alerts")
    op.drop_column("alerts", "dedupe_key")
