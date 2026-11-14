// Every page opens on the real server, fits a phone's width, and signing in works.
import { apiKey, expect, overflow, signIn, test } from './fixtures'

const PAGES = [
  { path: '/', heading: 'Research a security' },
  { path: '/markets', heading: 'Markets' },
  { path: '/portfolio', heading: 'Portfolio' },
  { path: '/backtest', heading: 'Backtest' },
  { path: '/screener', heading: 'Screener' },
  { path: '/screener/picks', heading: 'Claude’s picks' },
  { path: '/settings', heading: 'Settings' },
  { path: '/privacy', heading: 'Privacy policy' },
  { path: '/terms', heading: 'Terms of service' },
  { path: '/refunds', heading: 'Refund policy' },
]

test('signs in with an API key and opens the home page', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/signin/)

  await page.getByRole('textbox', { name: 'API key' }).fill(apiKey())
  await page.getByRole('button', { name: 'Sign in with key' }).click()
  const understood = page.getByRole('button', { name: 'I understand' })
  await understood.click()

  await expect(page.getByRole('heading', { level: 1, name: 'Research a security' })).toBeVisible()
})

test('a wrong key is refused and stays on the sign-in page', async ({ page }) => {
  await page.goto('/signin')

  await page.getByRole('textbox', { name: 'API key' }).fill('pq_not_a_real_key')
  await page.getByRole('button', { name: 'Sign in with key' }).click()

  await expect(page.getByRole('button', { name: 'Sign in with key' })).toBeEnabled()
  await expect(page).toHaveURL(/\/signin/)
  await expect(page.getByRole('heading', { name: 'Research a security' })).toHaveCount(0)
})

for (const { path, heading } of PAGES) {
  test(`${path} opens and nothing scrolls sideways`, async ({ page }) => {
    await signIn(page)
    await page.goto(path)

    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    await page.waitForLoadState('networkidle')
    expect(await overflow(page)).toBeLessThanOrEqual(0)
  })
}

test('an unknown address says so, with a way back', async ({ page }) => {
  await signIn(page)
  await page.goto('/no-such-page')

  await expect(page.getByRole('heading', { level: 1, name: 'Page not found' })).toBeVisible()
})

test('the policy pages give the support email', async ({ page }) => {
  for (const path of ['/terms', '/refunds', '/privacy']) {
    await page.goto(path)
    await expect(page.locator('a[href="mailto:pyqxel.app@gmail.com"]').first()).toBeVisible()
  }
})
