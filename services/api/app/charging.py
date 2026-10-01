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
                       target_soc_pct: float, stations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter for compatibility/reachability then rank 70% distance + 30% price."""
    if range_km < 0 or onboard_kw <= 0 or capacity_kwh <= 0 or not (0 <= soc_pct <= 100) or not (0 < target_soc_pct <= 100):
        raise ValueError("invalid charging recommendation inputs")
    energy = max(0.0, (target_soc_pct - soc_pct) / 100.0 * capacity_kwh)
    candidates = []
    for station in stations:
        if station["connector_type"] != connector_type or int(station["available_ports"]) <= 0:
            continue
        distance = haversine_km(latitude, longitude, float(station["latitude"]), float(station["longitude"]))
        if distance > range_km:
            continue
        power = min(float(station["power_kw"]), onboard_kw)
        efficiency = 0.9
        candidates.append({**station, "distance_km": round(distance, 1), "estimated_charge_minutes": round(energy / (power * efficiency) * 60),
                           "estimated_cost_inr": round(energy * float(station["price_per_kwh_inr"]), 2), "energy_kwh": round(energy, 2),
                           "_score_distance": distance, "_score_price": float(station["price_per_kwh_inr"]),
                           "reason": f"Reachable within current range with compatible {connector_type} connector and {station['available_ports']} available ports; time assumes 90% charging efficiency and constant power"})
    if not candidates:
        return []
    max_distance = max((candidate["_score_distance"] for candidate in candidates), default=1) or 1
    max_price = max((candidate["_score_price"] for candidate in candidates), default=1) or 1
    for candidate in candidates:
        distance_component = 1 - candidate["_score_distance"] / max_distance
        price_component = 1 - candidate["_score_price"] / max_price
        candidate["recommendation_score"] = round(100 * (0.7 * distance_component + 0.3 * price_component), 1)
        del candidate["_score_distance"], candidate["_score_price"]
    return sorted(candidates, key=lambda candidate: (-candidate["recommendation_score"], candidate["distance_km"]))
