import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CircleMarker, MapContainer, Popup, TileLayer } from 'react-leaflet';
import { apiRequest, clearAccessToken, onSessionExpired, storeAccessToken } from './api';
import 'leaflet/dist/leaflet.css';
import './style.css';

type Summary = { total: number; active: number; open_alerts: number; critical_alerts: number };
type Vehicle = {
  vehicle_id: string; latitude: number; longitude: number; soc_pct: number; soh_pct: number;
  range_km: number; speed_kmh: number; battery_health_category: string; timestamp: string;
};
type ChargingOption = {
  station_code: string; name: string; distance_km: number; estimated_charge_minutes: number;
  estimated_cost_inr: number; estimated_grid_energy_kwh: number; price_per_kwh_inr: number;
  savings_vs_best_inr: number; reason: string;
};
type ChargePlanItem = Vehicle & {
  battery_temp_c: number; battery_health_reasons: string[]; charge_timing: string;
  charge_timing_label: string; safety_hold: boolean; best_station: ChargingOption | null;
  alternatives: ChargingOption[];
};
type ChargePlan = {
  items: ChargePlanItem[]; count: number; target_soc_pct: number; limitations: string;
  reporting_vehicle_count: number; truncated: boolean;
  status_counts: { charge_now: number; plan_soon: number; service_review: number; charging: number; monitor: number };
};
type Alert = { id: string; vehicle_id: string; alert_type: string; severity: string; message: string; status: string; created_at: string };
type RangeEstimate = { estimated_range_km: number; simulator_reported_range_km: number; method: string; limitations: string };
type Analytics = { summary: { events: number; vehicle_count: number; avg_consumption_kwh_per_100km: number | null } };
type BatteryHealth = { category: string; soh_pct: number; battery_temp_c: number; degradation_risk: string; reasons: string[] };
type Station = { station_code: string; name: string; latitude: number; longitude: number; connector_type: string; power_kw: number; available_ports: number; price_per_kwh_inr: number };
type SystemHealth = { status: string; postgres: string; mongodb: string; redis: string };

function App() {
  const [summary, setSummary] = useState<Summary>();
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [chargePlan, setChargePlan] = useState<ChargePlanItem[]>([]);
  const [planLimitations, setPlanLimitations] = useState('Waiting for fleet telemetry.');
  const [planTotals, setPlanTotals] = useState<ChargePlan['status_counts']>();
  const [planReportingCount, setPlanReportingCount] = useState(0);
  const [planTruncated, setPlanTruncated] = useState(false);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Vehicle>();
  const [token, setToken] = useState(() => window.localStorage.getItem('evfleet-token') ?? '');
  const [loginError, setLoginError] = useState('');
  const [sessionExpired, setSessionExpired] = useState(false);
  const [targetSoc, setTargetSoc] = useState(80);
  const [rangeEstimate, setRangeEstimate] = useState<RangeEstimate>();
  const [chargingOptions, setChargingOptions] = useState<ChargingOption[]>([]);
  const [analytics, setAnalytics] = useState<Analytics>();
  const [batteryHealth, setBatteryHealth] = useState<BatteryHealth>();
  const [stations, setStations] = useState<Station[]>([]);
  const [systemHealth, setSystemHealth] = useState<SystemHealth>();

  useEffect(() => onSessionExpired(() => {
    setToken(''); setSessionExpired(true); setSummary(undefined); setVehicles([]);
    setChargePlan([]); setAlerts([]); setSelected(undefined); setBatteryHealth(undefined);
    setStations([]); setSystemHealth(undefined); setAnalytics(undefined);
    setRangeEstimate(undefined); setChargingOptions([]);
  }), []);

  useEffect(() => {
    if (!token) return;
    let live = true;
    const refresh = async () => {
      try {
        const [s, v, p, a, st, h] = await Promise.all([
          apiRequest('/fleet/summary'), apiRequest('/live-vehicles?limit=2000'),
          apiRequest(`/fleet/charging-plan?limit=500&target_soc_pct=${targetSoc}`),
          apiRequest('/alerts?limit=100&status=open'), apiRequest('/charging-stations?limit=50'),
          apiRequest('/system/health'),
        ]);
        if (![s, v, p, a, st, h].every((response) => response.ok)) throw new Error('API request failed');
        const [sd, vd, pd, ad, std, hd] = await Promise.all([s.json(), v.json(), p.json(), a.json(), st.json(), h.json()]);
        if (live) {
          setSummary(sd); setVehicles(vd.items); setChargePlan(pd.items);
          setPlanLimitations(pd.limitations); setPlanTotals(pd.status_counts);
          setPlanReportingCount(pd.reporting_vehicle_count); setPlanTruncated(pd.truncated);
          setAlerts(ad.items); setStations(std.items);
          setSystemHealth(hd); setError('');
        }
      } catch { if (live) setError('API unavailable. Confirm the local services are running.'); }
    };
    void refresh(); const timer = window.setInterval(refresh, 5000);
    return () => { live = false; window.clearInterval(timer); };
  }, [token, targetSoc]);

  useEffect(() => {
    if (!token) return;
    apiRequest('/analytics/consumption?period_hours=24&limit=5')
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then(setAnalytics).catch(() => setAnalytics(undefined));
  }, [token]);

  useEffect(() => {
    if (!selected || !token) { setRangeEstimate(undefined); setChargingOptions([]); setBatteryHealth(undefined); return; }
    const vehicleId = encodeURIComponent(selected.vehicle_id);
    Promise.all([
      apiRequest(`/vehicles/${vehicleId}/range-estimate`),
      apiRequest(`/vehicles/${vehicleId}/charging-recommendations?target_soc_pct=${targetSoc}`),
      apiRequest(`/vehicles/${vehicleId}/battery-health`),
    ]).then(async ([r, c, b]) => {
      if (!r.ok || !c.ok || !b.ok) throw new Error('Detail request failed');
      return Promise.all([r.json(), c.json(), b.json()]);
    }).then(([r, c, b]) => { setRangeEstimate(r); setChargingOptions(c.items); setBatteryHealth(b); })
      .catch(() => { setRangeEstimate(undefined); setChargingOptions([]); setBatteryHealth(undefined); });
  }, [selected, token, targetSoc]);

  const signIn = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setLoginError('');
    const data = new FormData(event.currentTarget);
    try {
      const response = await apiRequest('/auth/token', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: data.get('email'), password: data.get('password') }),
      }, true);
      if (!response.ok) throw new Error('Sign in failed');
      const result = await response.json() as { access_token?: string; token_type?: string };
      if (!result.access_token || result.token_type?.toLowerCase() !== 'bearer') throw new Error('Invalid login response');
      storeAccessToken(result.access_token); setSessionExpired(false); setToken(result.access_token);
    } catch { setLoginError('Invalid credentials or API unavailable.'); }
  };

  const signOut = () => {
    clearAccessToken(); setToken(''); setSummary(undefined); setVehicles([]); setChargePlan([]);
    setAlerts([]); setSelected(undefined); setBatteryHealth(undefined); setStations([]);
    setSystemHealth(undefined); setAnalytics(undefined); setRangeEstimate(undefined); setChargingOptions([]);
  };

  const actionCounts = useMemo(() => ({
    now: planTotals?.charge_now ?? chargePlan.filter((item) => item.charge_timing === 'charge_now').length,
    soon: planTotals?.plan_soon ?? chargePlan.filter((item) => item.charge_timing === 'plan_soon').length,
    review: planTotals?.service_review ?? chargePlan.filter((item) => item.safety_hold).length,
  }), [chargePlan, planTotals]);

  if (!token) return <div className="login-screen"><form className="login-card" onSubmit={signIn}>
    <div className="brand-icon">EV</div><p className="eyebrow">INDEPENDENT EV FLEET INTELLIGENCE</p>
    <h1>Fleet charging, made clear</h1><p>Sign in to review charging priorities, battery health, and remaining range.</p>
    {sessionExpired && <div className="error-banner">Your session expired. Sign in again to continue.</div>}
    <label>Email<input name="email" type="email" required defaultValue="operator@demo.local" /></label>
    <label>Password<input name="password" type="password" required autoComplete="current-password" /></label>
    {loginError && <div className="error-banner">{loginError}</div>}
    <button className="sign-in">Sign in</button><small>Use the demo operator credentials configured in your local .env file.</small>
  </form></div>;

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-icon">EV</div><div><b>EV FLEET</b><small>CHARGING INTELLIGENCE</small></div></div>
      <div className="nav-label">WORKSPACE</div>
      <a className="nav active" href="#charging-plan">◉ <span>Charging plan</span></a>
      <a className="nav" href="#vehicles">▤ <span>Vehicles</span></a>
      <a className="nav" href="#alerts">⚑ <span>Alerts</span></a>
      <a className="nav" href="#stations"><span>Charging stations</span></a>
      <a className="nav" href="#system-health"><span>System health</span></a>
      <div className="sidebar-bottom"><span className="online-dot" /> Live telemetry <small>Refreshes every 5 sec</small></div>
    </aside>
    <main className="main-content">
      <header className="topbar"><div><div className="breadcrumb">FLEETS / <b>DEMO FLEET</b></div><h1>Charging and battery overview</h1></div>
        <div className="top-right"><span className="live-pill"><i /> LIVE</span><button className="logout" onClick={signOut}>Sign out</button><span className="avatar">OP</span></div>
      </header>
      {error && <div className="error-banner">{error}</div>}
      <section className="metrics">
        <Metric title="FLEET VEHICLES" value={summary?.total.toLocaleString() ?? '—'} detail={`${summary?.active.toLocaleString() ?? '—'} active`} icon="▣" />
        <Metric title="REPORTING NOW" value={vehicles.length.toLocaleString()} detail="Vehicles with live telemetry" icon="⌁" />
        <Metric title="CHARGE NOW" value={actionCounts.now.toLocaleString()} detail="Low charge or range" icon="↯" danger={actionCounts.now > 0} />
        <Metric title="BATTERY SERVICE REVIEW" value={actionCounts.review.toLocaleString()} detail="Fault or high temperature hold" icon="!" danger={actionCounts.review > 0} />
      </section>
      <section className="history-strip">
        <div><small>HISTORICAL TELEMETRY · LAST 24 HOURS</small><b>{analytics?.summary.events.toLocaleString() ?? '—'} events</b></div>
        <div><small>VEHICLES IN WINDOW</small><b>{analytics?.summary.vehicle_count.toLocaleString() ?? '—'}</b></div>
        <div><small>AVG ENERGY CONSUMPTION</small><b>{analytics?.summary.avg_consumption_kwh_per_100km?.toFixed(1) ?? '—'} <i>kWh / 100 km</i></b></div>
        <span>On-demand historical aggregation</span>
      </section>

      <section className="panel charge-plan-panel" id="charging-plan">
        <div className="panel-heading plan-heading"><div><h2>Fleet charging plan</h2><p>Urgency first; feasible charging options sorted by estimated energy bill</p></div>
          <label className="target-control">Charge target <select value={targetSoc} onChange={(event) => setTargetSoc(Number(event.target.value))}><option value={70}>70% SoC</option><option value={80}>80% SoC</option><option value={90}>90% SoC</option></select></label>
        </div>
        <div className="plan-summary"><span className="plan-chip urgent">{actionCounts.now} charge now</span><span className="plan-chip soon">{actionCounts.soon} plan soon</span><span className="plan-chip review">{actionCounts.review} service review</span><span className="plan-chip charging-chip">{planTotals?.charging ?? chargePlan.filter((item) => item.charge_timing === 'charging').length} charging</span><span className="plan-live-count">Showing the top {Math.min(chargePlan.length, 100)} of {planReportingCount} indexed reporting vehicles{planTruncated ? ' · more available through the API' : ''}</span></div>
        <div className="table-wrap plan-table-wrap"><table className="plan-table"><thead><tr><th>VEHICLE</th><th>WHEN</th><th>BATTERY</th><th>CHARGE / RANGE</th><th>LOWEST-COST STATION</th><th>ESTIMATED BILL</th></tr></thead>
          <tbody>{chargePlan.slice(0, 100).map((item) => <tr key={item.vehicle_id} className="plan-row" onClick={() => setSelected(item)} tabIndex={0} onKeyDown={(event) => { if (event.key === 'Enter') setSelected(item); }}>
            <td className="mono">{item.vehicle_id}</td><td><span className={`plan-status ${item.charge_timing}`}>{item.charge_timing_label}</span></td>
            <td><b className={`health-category ${item.battery_health_category}`}>{item.battery_health_category}</b><small className="cell-sub">SoH {item.soh_pct.toFixed(1)}% · {item.battery_temp_c.toFixed(0)}°C</small></td>
            <td><b>{item.soc_pct.toFixed(0)}% SoC</b><small className="cell-sub">{item.range_km.toFixed(0)} km remaining</small></td>
            <td>{item.safety_hold ? <span className="cell-sub">Hold: inspect battery warning</span> : item.best_station ? <><b>{item.best_station.name}</b><small className="cell-sub">{item.best_station.distance_km.toFixed(1)} km · ₹{item.best_station.price_per_kwh_inr.toFixed(2)}/kWh</small></> : <span className="cell-sub">No reachable compatible station</span>}</td>
            <td>{item.best_station ? <><b>₹{item.best_station.estimated_cost_inr.toFixed(2)}</b><small className="cell-sub">{item.best_station.estimated_charge_minutes} min to target</small></> : '—'}</td>
          </tr>)}{chargePlan.length === 0 && <tr><td colSpan={6} className="empty">Waiting for vehicles to report telemetry.</td></tr>}</tbody>
        </table></div>
        <p className="plan-limitations">{planLimitations} Prices are current seeded flat rates; costs exclude traffic, route uncertainty, and station fees.</p>
      </section>

      <section className="content-grid"><div className="panel map-panel"><div className="panel-heading"><div><h2>Live vehicle map</h2><p>Current synthetic vehicle positions · Bengaluru</p></div><span className="count-tag">{vehicles.length} reporting</span></div>
        <MapContainer center={[12.9716, 77.5946]} zoom={11} scrollWheelZoom className="fleet-map"><TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
          {vehicles.map((vehicle) => <CircleMarker key={vehicle.vehicle_id} center={[vehicle.latitude, vehicle.longitude]} radius={selected?.vehicle_id === vehicle.vehicle_id ? 9 : 6} pathOptions={{ color: vehicle.soc_pct <= 20 || vehicle.range_km <= 35 ? '#f05d5e' : '#24a47b', fillColor: vehicle.soc_pct <= 20 || vehicle.range_km <= 35 ? '#f05d5e' : '#24a47b', fillOpacity: .85 }} eventHandlers={{ click: () => setSelected(vehicle) }}>
            <Popup><b>{vehicle.vehicle_id}</b><br />Charge {vehicle.soc_pct.toFixed(1)}% · {vehicle.range_km.toFixed(0)} km range<br />Battery {vehicle.battery_health_category}</Popup>
          </CircleMarker>)}</MapContainer>
      </div><div className="panel vehicle-panel" id="vehicles"><div className="panel-heading"><div><h2>Vehicles</h2><p>Select a vehicle for detailed health and range</p></div><span className="count-tag">{vehicles.length}</span></div>
        <div className="vehicle-list">{vehicles.slice(0, 25).map((vehicle) => <button className={`vehicle-row ${selected?.vehicle_id === vehicle.vehicle_id ? 'chosen' : ''}`} key={vehicle.vehicle_id} onClick={() => setSelected(vehicle)}>
          <span className={`vehicle-glyph ${vehicle.soc_pct <= 20 || vehicle.range_km <= 35 ? 'low' : ''}`}>EV</span><span className="vehicle-meta"><b>{vehicle.vehicle_id}</b><small>{vehicle.battery_health_category} · {vehicle.speed_kmh.toFixed(0)} km/h</small></span><span className="soc"><b>{vehicle.soc_pct.toFixed(0)}%</b><small>{vehicle.range_km.toFixed(0)} km</small></span>
        </button>)}{vehicles.length === 0 && <div className="empty">Waiting for telemetry from the simulator…</div>}</div>
      </div></section>

      {selected && <section className="panel detail-panel"><div className="panel-heading"><div><h2>{selected.vehicle_id} · Vehicle health and charge options</h2><p>Updated from the latest synthetic telemetry and retained samples</p></div><button className="close-detail" onClick={() => setSelected(undefined)}>Clear selection</button></div>
        <div className="detail-content"><div className="detail-stat"><small>BATTERY HEALTH</small><b className={`health-category ${batteryHealth?.category ?? selected.battery_health_category}`}>{batteryHealth?.category ?? selected.battery_health_category}</b><span>SoH {(batteryHealth?.soh_pct ?? selected.soh_pct).toFixed(1)}% · {(batteryHealth?.battery_temp_c ?? 0).toFixed(0)}°C</span><small>{batteryHealth?.reasons.join(', ') || 'No active health rule'}</small></div>
          <div className="detail-stat"><small>ESTIMATED RANGE</small><b>{rangeEstimate?.estimated_range_km ?? '—'} km</b><span>{rangeEstimate?.method ?? 'Loading estimate'}</span><small>{rangeEstimate?.limitations}</small></div>
          <div className="charging-options"><div className="options-heading"><b>Lowest-cost compatible stops</b><span>to {targetSoc}% SoC</span></div>{chargingOptions.slice(0, 3).map((option) => <div className="charger-row" key={option.station_code}>
            <span><b>{option.name}</b><small>{option.distance_km.toFixed(1)} km · {option.estimated_charge_minutes} min · ₹{option.price_per_kwh_inr.toFixed(2)}/kWh</small></span><strong>₹{option.estimated_cost_inr.toFixed(2)}</strong>
          </div>)}{chargingOptions.length === 0 && <small>No safe, reachable compatible charger is currently available.</small>}</div>
        </div><div className="detail-note">{rangeEstimate?.limitations} {batteryHealth?.degradation_risk && `Battery risk: ${batteryHealth.degradation_risk}.`} Charging prices are flat seeded estimates.</div>
      </section>}

      <section className="service-grid"><section className="panel stations-panel" id="stations"><div className="panel-heading"><div><h2>Charging stations</h2><p>Active station connectors and seeded prices</p></div><span className="count-tag">{new Set(stations.map((station) => station.station_code)).size} stations</span></div>
        <div className="station-list">{stations.slice(0, 8).map((station) => <article className="station-row" key={`${station.station_code}-${station.connector_type}`}><div><b>{station.name}</b><small>{station.station_code} · {station.connector_type} · {station.power_kw} kW</small></div><span>{station.available_ports} ports · ₹{station.price_per_kwh_inr.toFixed(2)}/kWh</span></article>)}{stations.length === 0 && <p className="empty">Loading station inventory…</p>}</div>
      </section><section className="panel health-panel" id="system-health"><div className="panel-heading"><div><h2>System health</h2><p>Readiness checks · refreshes every five seconds</p></div><span className={`health-status ${systemHealth?.status ?? 'loading'}`}>{systemHealth?.status ?? 'loading'}</span></div>
        <div className="health-list">{(['postgres', 'mongodb', 'redis'] as const).map((service) => <div className="health-row" key={service}><span>{service}</span><b className={systemHealth?.[service] ?? 'loading'}>{systemHealth?.[service] ?? 'checking'}</b></div>)}</div>
      </section></section>

      <section className="panel alerts-panel" id="alerts"><div className="panel-heading"><div><h2>Recent open alerts</h2><p>Battery and operational rule signals</p></div><a href="http://localhost:8000/docs" target="_blank" rel="noreferrer">API docs ↗</a></div>
        <div className="table-wrap"><table><thead><tr><th>VEHICLE</th><th>ALERT</th><th>DETAIL</th><th>SEVERITY</th><th>CREATED</th></tr></thead><tbody>{alerts.slice(0, 8).map((alert) => <tr key={alert.id}><td className="mono">{alert.vehicle_id}</td><td>{alert.alert_type.replaceAll('_', ' ')}</td><td>{alert.message}</td><td><span className={`severity ${alert.severity}`}>{alert.severity}</span></td><td>{new Date(alert.created_at).toLocaleTimeString()}</td></tr>)}{alerts.length === 0 && <tr><td colSpan={5} className="empty">No open alerts</td></tr>}</tbody></table></div>
      </section>
      <footer>EV Fleet Intelligence <span>·</span> Independent synthetic-data prototype <span>·</span> Charging costs, battery health, and range are estimates</footer>
    </main>
  </div>;
}

function Metric({ title, value, detail, icon, danger = false }: { title: string; value: string; detail: string; icon: string; danger?: boolean }) {
  return <article className="metric-card"><div className={`metric-icon ${danger ? 'danger' : ''}`}>{icon}</div><div className="metric-title">{title}</div><div className="metric-value">{value}</div><div className="metric-detail">{detail}</div></article>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
