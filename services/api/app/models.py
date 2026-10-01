"""Normalised transactional schema; high-volume telemetry lives outside PostgreSQL."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Fleet(Base):
    __tablename__ = "fleets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    region: Mapped[str] = mapped_column(String(80), default="Bengaluru")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)


class FleetMembership(Base):
    __tablename__ = "fleet_memberships"
    __table_args__ = (UniqueConstraint("user_id", "fleet_id", name="uq_user_fleet_membership"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    fleet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fleets.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)


class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (Index("ix_vehicles_fleet_status", "fleet_id", "status"),)
    vehicle_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    fleet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fleets.id", ondelete="CASCADE"), index=True)
    synthetic_vin: Mapped[str] = mapped_column(String(17), unique=True)
    make: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(80))
    model_year: Mapped[int] = mapped_column(Integer)
    battery_capacity_kwh: Mapped[float] = mapped_column(Float)
    connector_type: Mapped[str] = mapped_column(String(32))
    onboard_charging_kw: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChargingStation(Base):
    __tablename__ = "charging_stations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    station_code: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    operator: Mapped[str] = mapped_column(String(120))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StationConnector(Base):
    __tablename__ = "station_connectors"
    __table_args__ = (UniqueConstraint("station_id", "connector_type", name="uq_station_connector_type"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("charging_stations.id", ondelete="CASCADE"), index=True)
    connector_type: Mapped[str] = mapped_column(String(32))
    power_kw: Mapped[float] = mapped_column(Float)
    available_ports: Mapped[int] = mapped_column(Integer, default=0)
    price_per_kwh_inr: Mapped[float] = mapped_column(Float)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_fleet_status_created", "fleet_id", "status", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fleet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fleets.id", ondelete="CASCADE"), index=True)
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicles.vehicle_id", ondelete="CASCADE"), index=True)
    alert_type: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="open")
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChargingSession(Base):
    __tablename__ = "charging_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicles.vehicle_id", ondelete="CASCADE"), index=True)
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("charging_stations.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    energy_kwh: Mapped[float] = mapped_column(Float, default=0)
    total_cost_inr: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    event_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
