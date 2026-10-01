import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CircleMarker, MapContainer, Popup, TileLayer } from 'react-leaflet';
import { apiRequest, clearAccessToken, onSessionExpired, storeAccessToken } from './api';
import 'leaflet/dist/leaflet.css';
import './style.css';

type Summary = { total: number; active: number; open_alerts: number; critical_alerts: number };
type Vehicle = { vehicle_id: string; latitude: number; longitude: number; soc_pct: number; soh_pct: number; range_km: number; speed_kmh: number; battery_health_category: string; timestamp: string };
type Alert = { id: string; vehicle_id: string; alert_type: string; severity: string; message: string; status: string; created_at: string };
type RangeEstimate = { estimated_range_km: number; simulator_reported_range_km: number; method: string; limitations: string };
type ChargingOption = { station_code: string; name: string; distance_km: number; estimated_charge_minutes: number; estimated_cost_inr: number; reason: string };
type Analytics = { summary: { events: number; vehicle_count: number; avg_consumption_kwh_per_100km: number | null } };
type BatteryHealth = { category: string; soh_pct: number; battery_temp_c: number; degradation_risk: string; reasons: string[] };
type Station = { station_code: string; name: string; latitude: number; longitude: number; connector_type: string; power_kw: number; available_ports: number; price_per_kwh_inr: number };
type SystemHealth = { status: string; postgres: string; mongodb: string; redis: string };

function App() {
  const [summary, setSummary] = useState<Summary>();
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Vehicle>();
  const [token, setToken] = useState(() => window.localStorage.getItem('evfleet-token') ?? '');
  const [loginError, setLoginError] = useState('');
  const [sessionExpired, setSessionExpired] = useState(false);
  const [rangeEstimate, setRangeEstimate] = useState<RangeEstimate>();
  const [chargingOptions, setChargingOptions] = useState<ChargingOption[]>([]);
  const [analytics, setAnalytics] = useState<Analytics>();
  const [batteryHealth, setBatteryHealth] = useState<BatteryHealth>();
  const [stations, setStations] = useState<Station[]>([]);
  const [systemHealth, setSystemHealth] = useState<SystemHealth>();
  useEffect(() => onSessionExpired(() => {
    setToken('');
    setSessionExpired(true);
    setSummary(undefined);
    setVehicles([]);
    setAlerts([]);
    setSelected(undefined);
    setBatteryHealth(undefined);
    setStations([]);
    setSystemHealth(undefined);
    setAnalytics(undefined);
    setRangeEstimate(undefined);
    setChargingOptions([]);
  }), []);
  useEffect(() => {
    if (!token) return;
    let live = true;
    const refresh = async () => {
      try {
        const [s, v, a, st, h] = await Promise.all([apiRequest('/fleet/summary'), apiRequest('/live-vehicles?limit=2000'), apiRequest('/alerts?limit=100&status=open'), apiRequest('/charging-stations?limit=50'), apiRequest('/system/health')]);
        if (![s, v, a, st, h].every((response) => response.ok)) throw new Error('API request failed');
        const [sd, vd, ad, std, hd] = await Promise.all([s.json(), v.json(), a.json(), st.json(), h.json()]);
        if (live) { setSummary(sd); setVehicles(vd.items); setAlerts(ad.items); setStations(std.items); setSystemHealth(hd); setError(''); }
      } catch { if (live) setError('API unavailable. Confirm the local services are running.'); }
    };
    void refresh(); const timer = window.setInterval(refresh, 5000);
    return () => { live = false; window.clearInterval(timer); };
  }, [token]);
  useEffect(() => {
    if (!token) return;
    apiRequest('/analytics/consumption?period_hours=24&limit=5')
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then(setAnalytics).catch(() => setAnalytics(undefined));
  }, [token]);
  useEffect(() => {
    if (!selected || !token) { setRangeEstimate(undefined); setChargingOptions([]); setBatteryHealth(undefined); return; }
    Promise.all([apiRequest(`/vehicles/${selected.vehicle_id}/range-estimate`),apiRequest(`/vehicles/${selected.vehicle_id}/charging-recommendations`),apiRequest(`/vehicles/${selected.vehicle_id}/battery-health`)])
      .then(async ([r,c,b]) => { if (!r.ok || !c.ok || !b.ok) throw new Error('Detail request failed'); return Promise.all([r.json(),c.json(),b.json()]); })
      .then(([r,c,b]) => {setRangeEstimate(r);setChargingOptions(c.items);setBatteryHealth(b);})
      .catch(() => {setRangeEstimate(undefined);setChargingOptions([]);setBatteryHealth(undefined);});
  }, [selected, token]);
  const signIn = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setLoginError('');
    const data = new FormData(event.currentTarget);
    try {
      const response = await apiRequest('/auth/token', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:data.get('email'),password:data.get('password')})}, true);
      if (!response.ok) throw new Error('Sign in failed');
      const result = await response.json() as {access_token?: string; token_type?: string};
      if (!result.access_token || result.token_type?.toLowerCase() !== 'bearer') throw new Error('Login response did not contain a bearer token');
      storeAccessToken(result.access_token); setSessionExpired(false); setToken(result.access_token);
    } catch { setLoginError('Invalid credentials or API unavailable.'); }
  };
  if (!token) return <div className="login-screen"><form className="login-card" onSubmit={signIn}><div className="brand-icon">M</div><p className="eyebrow">MOTORQ · FLEET INTELLIGENCE</p><h1>Welcome back</h1><p>Sign in to view your fleet operations.</p>{sessionExpired&&<div className="error-banner">Your session expired. Sign in again to continue.</div>}<label>Email<input name="email" type="email" required defaultValue="operator@demo.local"/></label><label>Password<input name="password" type="password" required autoComplete="current-password"/></label>{loginError&&<div className="error-banner">{loginError}</div>}<button className="sign-in">Sign in</button><small>Use the demo operator credentials configured in the repository .env file.</small></form></div>;
  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><div className="brand-icon">M</div><div><b>MOTORQ</b><small>FLEET INTELLIGENCE</small></div></div><div className="nav-label">WORKSPACE</div><a className="nav active">◉ <span>Fleet overview</span></a><a className="nav" href="#vehicles">▤ <span>Vehicles</span></a><a className="nav" href="#alerts">⚑ <span>Alerts</span></a><a className="nav" href="#stations"><span>Charging stations</span></a><a className="nav" href="#system-health"><span>System health</span></a><div className="sidebar-bottom"><span className="online-dot"/> Live telemetry <small>Auto refresh · 5 sec</small></div></aside>
    <main className="main-content"><header className="topbar"><div><div className="breadcrumb">FLEETS / <b>DEMO FLEET</b></div><h1>Fleet overview</h1></div><div className="top-right"><span className="live-pill"><i/> LIVE</span><button className="logout" onClick={()=>{clearAccessToken();setToken('');setSummary(undefined);setVehicles([]);setAlerts([]);setSelected(undefined);setBatteryHealth(undefined);setStations([]);setSystemHealth(undefined);setAnalytics(undefined);setRangeEstimate(undefined);setChargingOptions([]);}}>Sign out</button><span className="avatar">OP</span></div></header>
      {error && <div className="error-banner">{error}</div>}
      <section className="metrics"><Metric title="TOTAL VEHICLES" value={summary?.total.toLocaleString() ?? '—'} detail={`${summary?.active.toLocaleString() ?? '—'} active`} icon="▣"/><Metric title="REPORTING NOW" value={vehicles.length.toLocaleString()} detail="Vehicles with live telemetry" icon="⌁"/><Metric title="OPEN ALERTS" value={summary?.open_alerts.toLocaleString() ?? '—'} detail="Across the fleet" icon="⚑"/><Metric title="CRITICAL ALERTS" value={summary?.critical_alerts.toLocaleString() ?? '—'} detail="Needs attention" icon="!" danger/></section>
      <section className="history-strip"><div><small>HISTORICAL TELEMETRY · LAST 24 HOURS</small><b>{analytics?.summary.events.toLocaleString() ?? '—'} events</b></div><div><small>VEHICLES IN WINDOW</small><b>{analytics?.summary.vehicle_count.toLocaleString() ?? '—'}</b></div><div><small>AVG ENERGY CONSUMPTION</small><b>{analytics?.summary.avg_consumption_kwh_per_100km?.toFixed(1) ?? '—'} <i>kWh / 100 km</i></b></div><span>On-demand MongoDB aggregation</span></section>
      <section className="content-grid"><div className="panel map-panel"><div className="panel-heading"><div><h2>Live vehicle map</h2><p>Latest vehicle positions · Bengaluru region</p></div><span className="count-tag">{vehicles.length} reporting</span></div><MapContainer center={[12.9716,77.5946]} zoom={11} scrollWheelZoom className="fleet-map"><TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"/>{vehicles.map((v)=><CircleMarker key={v.vehicle_id} center={[v.latitude,v.longitude]} radius={selected?.vehicle_id===v.vehicle_id?9:6} pathOptions={{color:v.soc_pct<=20?'#f05d5e':'#24a47b',fillColor:v.soc_pct<=20?'#f05d5e':'#24a47b',fillOpacity:.85}} eventHandlers={{click:()=>setSelected(v)}}><Popup><b>{v.vehicle_id}</b><br/>Charge {v.soc_pct.toFixed(1)}% · {v.range_km.toFixed(0)} km range</Popup></CircleMarker>)}</MapContainer></div>
        <div className="panel vehicle-panel" id="vehicles"><div className="panel-heading"><div><h2>Vehicles</h2><p>Latest reported state</p></div><span className="count-tag">{vehicles.length}</span></div><div className="vehicle-list">{vehicles.slice(0,25).map((v)=><button className={`vehicle-row ${selected?.vehicle_id===v.vehicle_id?'chosen':''}`} key={v.vehicle_id} onClick={()=>setSelected(v)}><span className={`vehicle-glyph ${v.soc_pct<=20?'low':''}`}>EV</span><span className="vehicle-meta"><b>{v.vehicle_id}</b><small>{v.battery_health_category} · {v.speed_kmh.toFixed(0)} km/h</small></span><span className="soc"><b>{v.soc_pct.toFixed(0)}%</b><small>{v.range_km.toFixed(0)} km</small></span></button>)}{vehicles.length===0&&<div className="empty">Waiting for telemetry from the simulator…</div>}</div></div></section>
      {selected&&<section className="panel detail-panel"><div className="panel-heading"><div><h2>{selected.vehicle_id} · Vehicle insight</h2><p>Computed from latest synthetic telemetry</p></div><button className="close-detail" onClick={()=>setSelected(undefined)}>Clear selection</button></div><div className="detail-content"><div className="detail-stat"><small>BATTERY HEALTH</small><b>{batteryHealth?.category ?? selected.battery_health_category}</b><span>SoH {(batteryHealth?.soh_pct ?? selected.soh_pct).toFixed(1)}%</span></div><div className="detail-stat"><small>RANGE BASELINE</small><b>{rangeEstimate?.estimated_range_km ?? '—'} km</b><span>{rangeEstimate?.method ?? 'Loading'}</span></div><div className="charging-options"><b>Smart charging recommendations</b>{chargingOptions.slice(0,3).map((option)=><div className="charger-row" key={option.station_code}><span><b>{option.name}</b><small>{option.distance_km} km · {option.estimated_charge_minutes} min</small></span><strong>₹{option.estimated_cost_inr}</strong></div>)}{chargingOptions.length===0&&<small>No compatible available charger found within reported range.</small>}</div></div><div className="detail-note">{rangeEstimate?.limitations}</div></section>}
      <section className="service-grid"><section className="panel stations-panel" id="stations"><div className="panel-heading"><div><h2>Charging stations</h2><p>Active station connectors from fleet registry</p></div><span className="count-tag">{new Set(stations.map((station)=>station.station_code)).size} stations</span></div><div className="station-list">{stations.slice(0,8).map((station)=><article className="station-row" key={`${station.station_code}-${station.connector_type}`}><div><b>{station.name}</b><small>{station.station_code} ? {station.connector_type} ? {station.power_kw} kW</small></div><span>{station.available_ports} ports ? ?{station.price_per_kwh_inr}/kWh</span></article>)}{stations.length===0&&<p className="empty">Loading station inventory?</p>}</div></section><section className="panel health-panel" id="system-health"><div className="panel-heading"><div><h2>System health</h2><p>Live readiness checks ? refreshes every five seconds</p></div><span className={`health-status ${systemHealth?.status ?? 'loading'}`}>{systemHealth?.status ?? 'loading'}</span></div><div className="health-list">{(['postgres','mongodb','redis'] as const).map((service)=><div className="health-row" key={service}><span>{service}</span><b className={systemHealth?.[service] ?? 'loading'}>{systemHealth?.[service] ?? 'checking'}</b></div>)}</div></section></section>
      <section className="panel alerts-panel" id="alerts"><div className="panel-heading"><div><h2>Recent open alerts</h2><p>Rule based operational signals</p></div><a href="http://localhost:8000/docs" target="_blank" rel="noreferrer">API docs ↗</a></div><div className="table-wrap"><table><thead><tr><th>VEHICLE</th><th>ALERT</th><th>DETAIL</th><th>SEVERITY</th><th>CREATED</th></tr></thead><tbody>{alerts.slice(0,8).map((a)=><tr key={a.id}><td className="mono">{a.vehicle_id}</td><td>{a.alert_type.replaceAll('_',' ')}</td><td>{a.message}</td><td><span className={`severity ${a.severity}`}>{a.severity}</span></td><td>{new Date(a.created_at).toLocaleTimeString()}</td></tr>)}{alerts.length===0&&<tr><td colSpan={5} className="empty">No open alerts</td></tr>}</tbody></table></div></section>
      <footer>EV-Fleet Intelligence <span>·</span> Synthetic demo telemetry <span>·</span> Health and range outputs are transparent rules based estimates</footer>
    </main></div>;
}
function Metric({title,value,detail,icon,danger=false}:{title:string;value:string;detail:string;icon:string;danger?:boolean}) {return <article className="metric-card"><div className={`metric-icon ${danger?'danger':''}`}>{icon}</div><div className="metric-title">{title}</div><div className="metric-value">{value}</div><div className="metric-detail">{detail}</div></article>;}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
