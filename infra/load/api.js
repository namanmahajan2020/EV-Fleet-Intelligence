import http from 'k6/http';
import { check } from 'k6';

const base = __ENV.API_BASE_URL || 'http://localhost:8000/api/v1';
export const options = {
  scenarios: {
    summary_load: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.LOAD_RATE || 5),
      timeUnit: '1s',
      duration: __ENV.LOAD_DURATION || '60s',
      preAllocatedVUs: Number(__ENV.LOAD_PREALLOCATED_VUS || 10),
      maxVUs: Number(__ENV.LOAD_MAX_VUS || 20),
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<200', 'p(99)<500'],
    checks: ['rate>0.99'],
  },
};

export function setup() {
  const response = http.post(`${base}/auth/token`, JSON.stringify({
    email: __ENV.DEMO_OPERATOR_EMAIL || 'operator@demo.local',
    password: __ENV.DEMO_OPERATOR_PASSWORD || '',
  }), { headers: { 'Content-Type': 'application/json' } });
  check(response, { 'login returned 200': (r) => r.status === 200 });
  if (response.status !== 200) throw new Error(`Login failed with ${response.status}`);
  return { token: response.json('access_token') };
}

export default function (data) {
  const response = http.get(`${base}/fleet/summary`, {
    headers: { Authorization: `Bearer ${data.token}` },
  });
  check(response, {
    'summary returned 200': (r) => r.status === 200,
    'summary reports seeded fleet': (r) => r.json('total') >= 1,
  });
}
