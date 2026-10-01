"""Idempotently seed a fleet, charger network, and configurable synthetic vehicle registry."""

import random

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db import SessionLocal
from app.models import (
    ChargingStation,
    Fleet,
    FleetMembership,
    Role,
    StationConnector,
    Tenant,
    User,
    Vehicle,
)
from app.security import hash_password

MAKES = (("Voltara", "City", 48.0), ("Northstar", "Cargo", 76.0), ("Luma", "Touring", 82.0))
CONNECTORS = (("CCS2", 0.65), ("TYPE2", 0.22), ("CHADEMO", 0.13))
STATIONS = (
    ("CHG-BLR-001", "Indiranagar Hub", 12.9784, 77.6408, "CCS2", 60.0, 18.5, 4),
    ("CHG-BLR-002", "MG Road Fast Charge", 12.9756, 77.6068, "CCS2", 120.0, 21.0, 2),
    ("CHG-BLR-003", "Koramangala Charge Point", 12.9352, 77.6245, "TYPE2", 22.0, 14.0, 6),
    ("CHG-BLR-004", "Whitefield Charging Plaza", 12.9698, 77.7500, "CCS2", 90.0, 19.5, 3),
    ("CHG-BLR-005", "Jayanagar Station", 12.9250, 77.5838, "CHADEMO", 50.0, 17.0, 2),
    ("CHG-BLR-006", "Yeshwanthpur Charge Hub", 13.0290, 77.5500, "CCS2", 50.0, 16.8, 4),
    ("CHG-BLR-007", "Electronic City Fast Charge", 12.8450, 77.6600, "CCS2", 60.0, 17.2, 4),
    ("CHG-BLR-008", "Hebbal North Hub", 13.0350, 77.5970, "CCS2", 75.0, 18.5, 3),
    ("CHG-BLR-009", "Kengeri Transit Charge", 12.9160, 77.4800, "TYPE2", 22.0, 13.8, 5),
    ("CHG-BLR-010", "Marathahalli Charge Point", 12.9590, 77.6970, "CCS2", 75.0, 18.0, 3),
    ("CHG-BLR-011", "Banashankari Station", 12.9250, 77.5700, "CHADEMO", 50.0, 16.4, 3),
    ("CHG-BLR-012", "Nagawara Fast Charge", 13.0430, 77.6200, "CCS2", 60.0, 18.2, 4),
    ("CHG-BLR-013", "Vijayanagar Charge Point", 12.9700, 77.5350, "TYPE2", 22.0, 14.1, 5),
    ("CHG-BLR-014", "Bellandur Charging Plaza", 12.9270, 77.6770, "CCS2", 90.0, 19.0, 3),
    ("CHG-BLR-015", "HSR Layout Station", 12.9110, 77.6380, "CHADEMO", 50.0, 15.8, 3),
)


def main() -> None:
    rng = random.Random(settings.simulator_seed)
    with SessionLocal.begin() as session:
        tenant = session.scalar(select(Tenant).where(Tenant.slug == "demo-operator"))
        if tenant is None:
            tenant = Tenant(slug="demo-operator", name="Synthetic EV Fleet Operator")
            session.add(tenant)
            session.flush()

        fleet = session.scalar(select(Fleet).where(Fleet.code == "fleet-demo"))
        if fleet is None:
            fleet = Fleet(tenant_id=tenant.id, code="fleet-demo", name="Bengaluru Operations", region="Bengaluru")
            session.add(fleet)
            session.flush()

        for role_name in ("fleet_manager", "analyst", "operator"):
            if session.scalar(select(Role).where(Role.name == role_name)) is None:
                session.add(Role(name=role_name))
        demo_user = session.scalar(select(User).where(User.email == settings.demo_operator_email))
        if demo_user is None:
            demo_user = User(email=settings.demo_operator_email, password_hash=hash_password(settings.demo_operator_password), display_name="Demo Fleet Manager")
            session.add(demo_user)
            session.flush()
            manager_role = session.scalar(select(Role).where(Role.name == "fleet_manager"))
            session.add(FleetMembership(user_id=demo_user.id, fleet_id=fleet.id, role_id=manager_role.id))
        else:
            # Keep the ignored local environment file authoritative when credentials rotate.
            demo_user.password_hash = hash_password(settings.demo_operator_password)

        for code, name, lat, lon, connector, power, rate, ports in STATIONS:
            station = session.scalar(select(ChargingStation).where(ChargingStation.station_code == code))
            if station is None:
                station = ChargingStation(
                    station_code=code, name=name, operator="Synthetic Charge Network", latitude=lat, longitude=lon
                )
                session.add(station)
                session.flush()
                session.add(
                    StationConnector(
                        station_id=station.id, connector_type=connector, power_kw=power,
                        available_ports=ports, price_per_kwh_inr=rate,
                    )
                )

        fleet_id = fleet.id

    # Stream bounded batches so seeding 100K rows does not retain the whole fleet in memory.
    batch_size = 2_000
    for first in range(1, settings.seed_vehicle_count + 1, batch_size):
        values = []
        last = min(first + batch_size, settings.seed_vehicle_count + 1)
        for number in range(first, last):
            make, model, capacity = rng.choice(MAKES)
            connector = rng.choices([entry[0] for entry in CONNECTORS], [entry[1] for entry in CONNECTORS])[0]
            vin = "SYN" + f"{number:014d}"  # 17 characters, with no real vehicle-owner identifier.
            values.append({
                "vehicle_id": f"EV-{number:06d}", "fleet_id": fleet_id, "synthetic_vin": vin,
                "make": make, "model": model, "model_year": rng.randint(2021, 2026),
                "battery_capacity_kwh": capacity, "connector_type": connector,
                "onboard_charging_kw": rng.choice((7.2, 11.0, 22.0)), "status": "active",
            })
        with SessionLocal.begin() as session:
            session.execute(insert(Vehicle).values(values).on_conflict_do_nothing(index_elements=["vehicle_id"]))

    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(Vehicle))
    print(f"Seed complete: requested={settings.seed_vehicle_count}, registered={total}, fleet={fleet_id}")


if __name__ == "__main__":
    main()
