import { expect, test } from '@playwright/test';

test('authenticated fleet workspace routes and live interactions', async ({ page }) => {
  const appFailures: string[] = [];
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

  await page.goto('/charging-plan');
  await page.getByLabel('Email').fill(process.env.DEMO_OPERATOR_EMAIL ?? '');
  await page.getByLabel('Password').fill(process.env.DEMO_OPERATOR_PASSWORD ?? '');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Charging plan' })).toBeVisible();
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
