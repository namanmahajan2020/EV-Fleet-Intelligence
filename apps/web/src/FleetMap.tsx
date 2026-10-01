import { CircleMarker, MapContainer, Popup, TileLayer } from 'react-leaflet';
import { Link } from 'react-router-dom';

type VehiclePoint = {
  vehicle_id: string;
  latitude: number;
  longitude: number;
  soc_pct: number;
  soh_pct?: number;
  range_km: number;
  battery_temp_c?: number;
  battery_health_category?: string;
  battery_health_reasons?: string[];
  charging?: boolean;
  fault_codes?: string[];
  timestamp?: string;
};

export type LiveFleet = { items: VehiclePoint[]; count: number; reporting_vehicle_count: number; sampled_limit: number; as_of: string };

const COLORS = { critical: '#e65f59', healthy: '#258765', charging: '#3f78b5' };

function vehicleStatus(vehicle: VehiclePoint) {
  const highRisk = vehicle.battery_health_category === 'critical'
    || vehicle.battery_health_category === 'watch'
    || vehicle.soc_pct <= 20
    || vehicle.range_km <= 35
    || (vehicle.battery_temp_c ?? 0) >= 60
    || (vehicle.fault_codes ?? []).some((code) => code === 'P0A80' || code === 'P1A10');
  if (highRisk) return { label: 'High risk', color: COLORS.critical };
  if (vehicle.charging) return { label: 'Charging', color: COLORS.charging };
  return { label: 'Healthy', color: COLORS.healthy };
}

export default function FleetMap({ fleet }: { fleet?: LiveFleet }) {
  const points = (fleet?.items ?? []).filter((vehicle) =>
    Number.isFinite(vehicle.latitude) && Number.isFinite(vehicle.longitude)
    && vehicle.latitude >= -90 && vehicle.latitude <= 90
    && vehicle.longitude >= -180 && vehicle.longitude <= 180,
  ).slice(0, 1000);

  return <div className="fleet-map" data-testid="vehicle-map" data-marker-limit={fleet?.sampled_limit ?? 1000} data-marker-count={points.length}>
    <MapContainer center={[12.9716, 77.5946]} zoom={10} scrollWheelZoom className="fleet-map-inner">
      <TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
      {points.map((vehicle) => {
        const status = vehicleStatus(vehicle);
        return <CircleMarker
          key={vehicle.vehicle_id}
          center={[vehicle.latitude, vehicle.longitude]}
          radius={6}
          pathOptions={{ color: status.color, fillOpacity: .8 }}
        >
          <Popup>
            <b>{vehicle.vehicle_id}</b><br />{vehicle.soc_pct}% SoC · {vehicle.range_km} km<br />
            <Link to={`/vehicles/${encodeURIComponent(vehicle.vehicle_id)}`}>Open vehicle</Link>
          </Popup>
        </CircleMarker>;
      })}
    </MapContainer>
    <div className="fleet-map-legend" aria-label="Vehicle marker status legend">
      <span><i className="critical" /> Critical / low / high risk</span>
      <span><i className="charging" /> Charging</span>
      <span><i className="healthy" /> Healthy / normal</span>
    </div>
    <div className="fleet-map-count" aria-live="polite">Showing {points.length.toLocaleString()} of {(fleet?.reporting_vehicle_count ?? 0).toLocaleString()} reporting vehicles · Refreshes every 5 sec</div>
  </div>;
}
