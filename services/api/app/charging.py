"""Small, deterministic charger selection functions with no web/database dependencies."""
from math import asin, cos, radians, sin, sqrt
from typing import Any


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    values = (lat1, lon1, lat2, lon2)
    if not (-90 <= lat1 <= 90 and -90 <= lat2 <= 90 and -180 <= lon1 <= 180 and -180 <= lon2 <= 180):
        raise ValueError("coordinates outside latitude/longitude domain")
    phi1, lam1, phi2, lam2 = map(radians, values)
    dphi, dlam = phi2 - phi1, lam2 - lam1
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlam / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


def recommend_stations(*, latitude: float, longitude: float, range_km: float, connector_type: str,
                       onboard_kw: float, capacity_kwh: float, soc_pct: float,
                       target_soc_pct: float, stations: list[dict[str, Any]],
                       consumption_kwh_per_100km: float = 18.0,
                       safety_reserve_km: float = 5.0) -> list[dict[str, Any]]:
    """Return feasible chargers ordered by estimated total energy bill, then detour.

    Cost includes energy used to reach the station and 90% charging efficiency.
    The range reserve is a simple safety margin; this does not model routes,
    traffic, station fees, or time-of-use tariffs.
    """
    if range_km < 0 or onboard_kw <= 0 or capacity_kwh <= 0 or not (0 <= soc_pct <= 100) or not (0 < target_soc_pct <= 100):
        raise ValueError("invalid charging recommendation inputs")
    if consumption_kwh_per_100km <= 0 or safety_reserve_km < 0:
        raise ValueError("invalid consumption or range reserve")
    energy_to_target = max(0.0, (target_soc_pct - soc_pct) / 100.0 * capacity_kwh)
    candidates = []
    for station in stations:
        if station["connector_type"] != connector_type or int(station["available_ports"]) <= 0:
            continue
        distance = haversine_km(latitude, longitude, float(station["latitude"]), float(station["longitude"]))
        if distance > max(0.0, range_km - safety_reserve_km):
            continue
        power = min(float(station["power_kw"]), onboard_kw)
        efficiency = 0.9
        travel_energy = distance * consumption_kwh_per_100km / 100.0
        battery_energy = energy_to_target + travel_energy
        grid_energy = battery_energy / efficiency
        price = float(station["price_per_kwh_inr"])
        candidates.append({
            **station,
            "distance_km": round(distance, 1),
            "estimated_charge_minutes": round(battery_energy / (power * efficiency) * 60),
            "estimated_cost_inr": round(grid_energy * price, 2),
            "energy_to_target_kwh": round(energy_to_target, 2),
            "estimated_travel_energy_kwh": round(travel_energy, 2),
            "estimated_grid_energy_kwh": round(grid_energy, 2),
            "price_per_kwh_inr": price,
            "reason": (
                f"Lowest estimated energy bill among reachable stations; compatible {connector_type} connector, "
                f"{station['available_ports']} available ports, and {safety_reserve_km:g} km range reserve. "
                "Cost includes estimated detour energy and 90% charging efficiency."
            ),
        })
    if not candidates:
        return []
    candidates.sort(key=lambda candidate: (candidate["estimated_cost_inr"], candidate["distance_km"], candidate["estimated_charge_minutes"]))
    cheapest = candidates[0]["estimated_cost_inr"]
    for candidate in candidates:
        candidate["savings_vs_best_inr"] = round(candidate["estimated_cost_inr"] - cheapest, 2)
    return candidates


def charging_timing(*, soc_pct: float, range_km: float, battery_temp_c: float,
                    fault_codes: list[str], is_charging: bool = False) -> dict[str, str | bool]:
    """Classify a transparent charge window and flag conditions needing service review."""
    safety_hold = battery_temp_c >= 60 or any(code in {"P0A80", "P1A10"} for code in fault_codes)
    if safety_hold:
        return {"status": "service_review", "label": "Service review before charging", "safety_hold": True}
    if is_charging:
        return {"status": "charging", "label": "Currently charging", "safety_hold": False}
    if soc_pct <= 20 or range_km <= 35:
        return {"status": "charge_now", "label": "Charge now", "safety_hold": False}
    if soc_pct <= 40 or range_km <= 80:
        return {"status": "plan_soon", "label": "Plan the next stop", "safety_hold": False}
    return {"status": "monitor", "label": "Monitor; charging can wait", "safety_hold": False}
