// Every page opens on the real server, fits a phone's width, and signing in works.
import { apiKey, expect, overflow, signIn, test } from './fixtures'

const PAGES = [
  { path: '/', heading: 'Research a Security' },
  { path: '/markets', heading: 'Markets' },
  { path: '/portfolio', heading: 'Portfolio' },
  { path: '/backtest', heading: 'Backtest' },
  { path: '/screener', heading: 'Screener' },
  { path: '/screener/picks', heading: 'Claude’s Picks' },
  { path: '/screener/refresh', heading: 'Refresh Schedule' },
  { path: '/settings', heading: 'Settings' },
  { path: '/privacy', heading: 'Privacy Policy' },
  { path: '/terms', heading: 'Terms of Service' },
  { path: '/refunds', heading: 'Refund Policy' },
]

test('signs in with an API key and opens the home page', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/signin/)

  await page.getByRole('textbox', { name: 'API key' }).fill(apiKey())
  await page.getByRole('button', { name: 'Sign in with key' }).click()
  const understood = page.getByRole('button', { name: 'I understand' })
  await understood.click()

  await expect(page.getByRole('heading', { level: 1, name: 'Research a Security' })).toBeVisible()
})

test('a wrong key is refused and stays on the sign-in page', async ({ page }) => {
  await page.goto('/signin')

  await page.getByRole('textbox', { name: 'API key' }).fill('pq_not_a_real_key')
  await page.getByRole('button', { name: 'Sign in with key' }).click()

  await expect(page.getByRole('button', { name: 'Sign in with key' })).toBeEnabled()
  await expect(page).toHaveURL(/\/signin/)
  await expect(page.getByRole('heading', { name: 'Research a Security' })).toHaveCount(0)
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

  await expect(page.getByRole('heading', { level: 1, name: 'Page Not Found' })).toBeVisible()
})

test('the policy pages give the support email', async ({ page }) => {
  for (const path of ['/terms', '/refunds', '/privacy']) {
    await page.goto(path)
    await expect(page.locator('a[href="mailto:pyqxel.app@gmail.com"]').first()).toBeVisible()
  }
})

test('the Settings link is marked while Settings is open, like the other tabs', async ({
  page,
}) => {
  await signIn(page)
  await page.goto('/settings')
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()

  // The top bar's button on a computer, the tab bar's tab on a phone: whichever shows.
  const settings = page.locator('header a[href="/settings"]:visible')
  const markets = page.locator('header a[href="/markets"]:visible')
  await expect(settings).toHaveClass(/active/)
  const look = (link: typeof settings) =>
    link.evaluate((el) => {
      const style = getComputedStyle(el)
      return `${style.color} ${style.backgroundColor}`
    })
  expect(await look(settings)).not.toBe(await look(markets))
})
