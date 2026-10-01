import { CircleMarker, MapContainer, Popup, TileLayer } from 'react-leaflet';
import { Link } from 'react-router-dom';

type VehiclePoint = { vehicle_id: string; latitude: number; longitude: number; soc_pct: number; range_km: number; charge_timing: string };
export default function FleetMap({ items }: { items: VehiclePoint[] }) {
  const points = items.filter((item) => Number.isFinite(item.latitude) && Number.isFinite(item.longitude)).slice(0, 100);
  return <div className="fleet-map"><MapContainer center={[12.9716, 77.5946]} zoom={10} scrollWheelZoom className="fleet-map-inner"><TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />{points.map((v) => <CircleMarker key={v.vehicle_id} center={[v.latitude, v.longitude]} radius={6} pathOptions={{ color: v.charge_timing === 'charge_now' ? '#e65f59' : '#258765', fillOpacity: .8 }}><Popup><b>{v.vehicle_id}</b><br />{v.soc_pct}% SoC · {v.range_km} km<br /><Link to={`/vehicles/${encodeURIComponent(v.vehicle_id)}`}>Open vehicle</Link></Popup></CircleMarker>)}</MapContainer></div>;
}
