// The screener and the portfolio, on the universe and holdings scripts/e2e_seed.py stored.
import { expect, signIn, test } from './fixtures'

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

test('the screener narrows to Nasdaq and sorts by a column', async ({ page }, info) => {
  test.skip(info.project.name === 'phone', 'The results table is checked at desktop width.')
  // The S&P 500, the default, needs the SEC's filings, which the tests do not reach, so
  // the screen starts from a saved one of everything.
  await page.addInitScript(() =>
    localStorage.setItem(
      'pyqxel.screen',
      JSON.stringify({ universe: 'all', sort: 'market_cap', descending: true }),
    ),
  )
  await page.goto('/screener')
  const universe = page.getByLabel('Universe')
  const symbols = page.locator('#matches table.data tbody tr td:nth-child(2)')

  await expect(symbols).toHaveText(['ALFA', 'BRAVO', 'CHARL', 'DELTA'])

  await universe.selectOption('nasdaq')
  await expect(symbols).toHaveText(['ALFA', 'CHARL'])

  await universe.selectOption('all')
  await page.getByRole('button', { name: 'P/E', exact: true }).click()
  await expect(symbols.first()).toHaveText('BRAVO')
})

test('the portfolio keeps fractional shares, and removing a holding waits for Save', async ({
  page,
}) => {
  await page.goto('/portfolio')
  const load = page.getByRole('button', { name: 'Load', exact: true })
  if (await load.isVisible().catch(() => false)) await load.click()

  await expect(page.getByLabel('Shares of ALFA')).toHaveValue('3.5')
  await page.getByRole('button', { name: 'Remove BRAVO' }).click()
  await expect(page.getByLabel('Shares of BRAVO')).toHaveCount(0)

  // Unsaved, so the holding is back after a reload.
  await page.reload()
  if (await load.isVisible().catch(() => false)) await load.click()
  await expect(page.getByLabel('Shares of BRAVO')).toBeVisible()
})
