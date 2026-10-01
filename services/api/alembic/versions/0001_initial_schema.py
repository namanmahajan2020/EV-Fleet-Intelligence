"""Initial relational fleet schema (frozen schema snapshot)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)
    op.create_table(
        "fleets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("region", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_fleets_tenant_id", "fleets", ["tenant_id"])
    op.create_index("ix_fleets_code", "fleets", ["code"], unique=True)
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(50), nullable=False, unique=True),
    )
    op.create_table(
        "fleet_memberships",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fleet_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fleets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=False),
        sa.UniqueConstraint("user_id", "fleet_id", name="uq_user_fleet_membership"),
    )
    op.create_index("ix_fleet_memberships_user_id", "fleet_memberships", ["user_id"])
    op.create_index("ix_fleet_memberships_fleet_id", "fleet_memberships", ["fleet_id"])
    op.create_index("ix_fleet_memberships_role_id", "fleet_memberships", ["role_id"])
    op.create_table(
        "vehicles",
        sa.Column("vehicle_id", sa.String(32), primary_key=True),
        sa.Column("fleet_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fleets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("synthetic_vin", sa.String(17), nullable=False, unique=True),
        sa.Column("make", sa.String(80), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("model_year", sa.Integer(), nullable=False),
        sa.Column("battery_capacity_kwh", sa.Float(), nullable=False),
        sa.Column("connector_type", sa.String(32), nullable=False),
        sa.Column("onboard_charging_kw", sa.Float(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_vehicles_fleet_id", "vehicles", ["fleet_id"])
    op.create_index("ix_vehicles_fleet_status", "vehicles", ["fleet_id", "status"])
    op.create_table(
        "charging_stations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("station_code", sa.String(48), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("operator", sa.String(120), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_charging_stations_station_code", "charging_stations", ["station_code"], unique=True)
    op.create_table(
        "station_connectors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("charging_stations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connector_type", sa.String(32), nullable=False),
        sa.Column("power_kw", sa.Float(), nullable=False),
        sa.Column("available_ports", sa.Integer(), nullable=False),
        sa.Column("price_per_kwh_inr", sa.Float(), nullable=False),
        sa.UniqueConstraint("station_id", "connector_type", name="uq_station_connector_type"),
    )
    op.create_index("ix_station_connectors_station_id", "station_connectors", ["station_id"])
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("fleet_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fleets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehicle_id", sa.String(32), sa.ForeignKey("vehicles.vehicle_id", ondelete="CASCADE"), nullable=False),
        sa.Column("alert_type", sa.String(48), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_alerts_fleet_id", "alerts", ["fleet_id"])
    op.create_index("ix_alerts_vehicle_id", "alerts", ["vehicle_id"])
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])
    op.create_index("ix_alerts_fleet_status_created", "alerts", ["fleet_id", "status", "created_at"])
    op.create_table(
        "charging_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vehicle_id", sa.String(32), sa.ForeignKey("vehicles.vehicle_id", ondelete="CASCADE"), nullable=False),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("charging_stations.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("energy_kwh", sa.Float(), nullable=False),
        sa.Column("total_cost_inr", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
    )
    op.create_index("ix_charging_sessions_vehicle_id", "charging_sessions", ["vehicle_id"])
    op.create_index("ix_charging_sessions_station_id", "charging_sessions", ["station_id"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_id", sa.String(100)),
        sa.Column("request_id", sa.String(80)),
        sa.Column("event_data", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("charging_sessions")
    op.drop_table("alerts")
    op.drop_table("station_connectors")
    op.drop_table("charging_stations")
    op.drop_table("vehicles")
    op.drop_table("fleet_memberships")
    op.drop_table("roles")
    op.drop_table("users")
    op.drop_table("fleets")
    op.drop_table("tenants")
