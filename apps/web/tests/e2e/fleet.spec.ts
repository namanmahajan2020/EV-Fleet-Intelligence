import { expect, test } from '@playwright/test';

test('authenticated fleet workspace routes and live interactions', async ({ page }) => {
  const appFailures: string[] = [];
  const liveMapResponses: Array<{ at: number; body: { items: Array<{ vehicle_id: string; latitude: number; longitude: number; timestamp: string; soc_pct: number; range_km: number; battery_temp_c?: number; battery_health_category?: string; fault_codes?: string[]; charging?: boolean }>; count: number; sampled_limit: number; reporting_vehicle_count: number; method: string } }> = [];
  page.on('pageerror', (error) => appFailures.push(`page: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error' && /uncaught|syntaxerror|typeerror/i.test(message.text())) appFailures.push(`console: ${message.text()}`);
  });
  page.on('response', (response) => {
    const sameOrigin = response.url().startsWith('http://localhost:5173/');
    if ((response.url().includes('/api/v1/') || sameOrigin) && [401, 404, 500].includes(response.status())) {
      appFailures.push(`HTTP ${response.status()}: ${response.url().split('?')[0]}`);
    }
  });
  page.on('response', async (response) => {
    if (response.url().includes('/api/v1/live-vehicles?limit=1000') && response.ok()) {
      liveMapResponses.push({ at: Date.now(), body: await response.json() });
    }
  });

  await page.goto('/charging-plan');
  await page.getByLabel('Email').fill(process.env.DEMO_OPERATOR_EMAIL ?? '');
  await page.getByLabel('Password').fill(process.env.DEMO_OPERATOR_PASSWORD ?? '');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Charging plan' })).toBeVisible();
  await expect(page.getByTestId('vehicle-map')).toBeVisible();
  await expect.poll(() => liveMapResponses.length, { timeout: 8_000 }).toBeGreaterThanOrEqual(2);
  expect(liveMapResponses[0].body.count).toBeLessThanOrEqual(1000);
  expect(liveMapResponses[0].body.items).toHaveLength(liveMapResponses[0].body.count);
  expect(liveMapResponses[0].body.sampled_limit).toBe(1000);
  expect(liveMapResponses[0].body.reporting_vehicle_count).toBeGreaterThanOrEqual(liveMapResponses[0].body.count);
  expect(liveMapResponses[0].body.method).toBe('bounded_most_recent_redis_latest_state_sample');
  expect(liveMapResponses[0].body.items.map((item) => Date.parse(item.timestamp))).toEqual(
    [...liveMapResponses[0].body.items.map((item) => Date.parse(item.timestamp))].sort((a, b) => b - a),
  );
  await expect.poll(() => liveMapResponses.some((response, index) => index > 0
    && response.at - liveMapResponses[index - 1].at >= 4000
    && response.at - liveMapResponses[index - 1].at < 8000
    && JSON.stringify(response.body.items.map((item) => [item.vehicle_id, item.latitude, item.longitude]))
      !== JSON.stringify(liveMapResponses[index - 1].body.items.map((item) => [item.vehicle_id, item.latitude, item.longitude]))), { timeout: 12_000 }).toBe(true);
  await expect.poll(async () => Number(await page.getByTestId('vehicle-map').getAttribute('data-marker-count'))).toBeLessThanOrEqual(1000);
  const map = page.locator('.fleet-map .leaflet-container');
  const dots = map.locator('.leaflet-overlay-pane path.leaflet-interactive');
  const markerCount = Number(await page.getByTestId('vehicle-map').getAttribute('data-marker-count'));
  const fixedRadius = async () => dots.evaluateAll((paths) => paths.every((path) => path.getAttribute('d')?.includes('a6,6')));
  const telemetryColorsMatch = async () => {
    const items = liveMapResponses.at(-1)?.body.items;
    if (!items) return false;
    const expected: Record<string, number> = {};
    for (const item of items) {
      const highRisk = item.battery_health_category === 'critical' || item.battery_health_category === 'watch'
        || item.soc_pct <= 20 || item.range_km <= 35 || (item.battery_temp_c ?? 0) >= 60
        || (item.fault_codes ?? []).some((code) => code === 'P0A80' || code === 'P1A10');
      const color = highRisk ? '#e65f59' : item.charging ? '#3f78b5' : '#258765';
      expected[color] = (expected[color] ?? 0) + 1;
    }
    const actual = await dots.evaluateAll((paths) => paths.reduce<Record<string, number>>((counts, path) => {
      const color = path.getAttribute('stroke') ?? '';
      counts[color] = (counts[color] ?? 0) + 1;
      return counts;
    }, {}));
    const stable = (counts: Record<string, number>) => JSON.stringify(Object.fromEntries(Object.entries(counts).sort(([left], [right]) => left.localeCompare(right))));
    return stable(expected) === stable(actual);
  };
  await expect(dots).toHaveCount(markerCount);
  expect(await fixedRadius()).toBe(true);
  await expect.poll(telemetryColorsMatch).toBe(true);
  await expect(map.locator('.fleet-cluster')).toHaveCount(0);
  for (let step = 0; step < 4; step += 1) await page.locator('.leaflet-control-zoom-out').click();
  await expect(dots).toHaveCount(markerCount);
  expect(await fixedRadius()).toBe(true);
  await expect(map.locator('.fleet-cluster')).toHaveCount(0);
  for (let step = 0; step < 8; step += 1) await page.locator('.leaflet-control-zoom-in').click();
  await expect(dots).toHaveCount(markerCount);
  expect(await fixedRadius()).toBe(true);
  await expect(map.locator('.fleet-cluster')).toHaveCount(0);
  const mapBox = await map.boundingBox();
  if (mapBox) {
    await page.mouse.move(mapBox.x + mapBox.width / 2, mapBox.y + mapBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(mapBox.x + mapBox.width / 2 - 140, mapBox.y + mapBox.height / 2 - 90, { steps: 8 });
    await page.mouse.up();
  }
  await expect(dots).toHaveCount(markerCount);
  await expect(map.locator('.fleet-cluster')).toHaveCount(0);
  await expect(page.locator('.nav.active')).toContainText('Charging plan');
  const vehicleNav = page.locator('.sidebar .nav', { hasText: 'Vehicles' });
  const navBackground = await vehicleNav.evaluate((node) => getComputedStyle(node).backgroundColor);
  await vehicleNav.hover();
  await expect.poll(() => vehicleNav.evaluate((node) => getComputedStyle(node).backgroundColor)).not.toBe(navBackground);

  const routes = [
    ['Vehicles', '/vehicles'], ['Battery health', '/battery-health'], ['Smart charging', '/smart-charging'],
    ['Charging stations', '/stations'], ['Alerts', '/alerts'], ['Analytics', '/analytics'],
    ['Predictions', '/predictions'], ['System health', '/system-health'],
  ];
  for (const [label, path] of routes) {
    await page.locator('.sidebar .nav', { hasText: label }).click();
    await expect(page).toHaveURL(new RegExp(`${path.replaceAll('/', '\\/')}$`));
    await expect(page.locator('.sidebar .nav.active')).toContainText(label);
  }

  await page.locator('.sidebar .nav', { hasText: 'Vehicles' }).click();
  await expect(page.getByRole('cell', { name: 'EV-000001' })).toBeVisible();
  await page.getByPlaceholder('Search vehicle ID').fill('EV-000001');
  await expect(page).toHaveURL(/search=EV-000001/);
  await expect(page.getByRole('link', { name: 'EV-000001' })).toBeVisible();
  await page.getByPlaceholder('Search vehicle ID').fill('');
  await expect(page).toHaveURL(/\/vehicles\?page=0$/);
  await expect(page.getByRole('link', { name: 'EV-000050' })).toBeVisible();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page).toHaveURL(/\/vehicles\?page=1$/);
  await expect(page.locator('.pagination')).toContainText('51–100 of');
  await page.getByRole('button', { name: 'Previous' }).click();
  await page.getByLabel('Fleet status').selectOption('low_battery');
  await expect(page.getByText('State and health filters are applied server-side to live state.')).toBeVisible();
  await page.getByLabel('Fleet status').selectOption('all');
  await page.getByRole('link', { name: 'EV-000001' }).click();
  await expect(page).toHaveURL(/\/vehicles\/EV-000001$/);
  await expect(page.getByRole('heading', { name: 'EV-000001' })).toBeVisible();

  await page.locator('.sidebar .nav', { hasText: 'Stations' }).click();
  await page.locator('.selectable-row').first().click();
  await expect(page.locator('.station-detail')).toBeVisible();

  await page.locator('.sidebar .nav', { hasText: 'Predictions' }).click();
  await page.getByLabel('Vehicle ID').fill('EV-000001');
  await page.getByRole('button', { name: 'Load vehicle telemetry' }).click();
  await expect(page.getByLabel('Requested distance km')).toBeVisible();
  await page.getByLabel('Requested distance km').fill('80');
  await page.getByRole('button', { name: 'Predict' }).click();
  await expect(page.getByRole('heading', { name: 'Input → prediction → explanation' })).toBeVisible();

  await page.locator('.sidebar .nav', { hasText: 'Smart charging' }).click();
  await page.getByLabel('Vehicle ID').fill('EV-000001');
  await page.getByRole('button', { name: 'Get recommendations' }).click();
  await expect(page.getByText('CURRENT SOC')).toBeVisible();

  await page.locator('.sidebar .nav', { hasText: 'Analytics' }).click();
  await page.getByLabel('Window').selectOption('168');
  await expect(page.getByRole('heading', { name: 'Fleet telemetry trends' })).toBeVisible();

  await page.goto('/vehicles/EV-000001');
  await expect(page.getByRole('heading', { name: 'EV-000001' })).toBeVisible();
  expect(appFailures, appFailures.join('\n')).toEqual([]);
});
