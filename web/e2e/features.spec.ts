// The screener and the portfolio, on the universe and holdings scripts/e2e_seed.py stored.
import { expect, signIn, test, type Page } from './fixtures'

/** Opens `portfolio`, saying yes when the page asks before pricing its holdings. */
async function openPortfolio(page: Page, portfolio: number): Promise<void> {
  await page.getByRole('button', { name: `Portfolio ${portfolio}`, exact: true }).click()
  const load = page.getByRole('button', { name: 'Load', exact: true })
  const holdings = page.locator('#holdings')
  await expect(load.or(holdings)).toBeVisible({ timeout: 15_000 })
  if (await load.isVisible()) await load.click()
  await expect(holdings).toBeVisible()
}

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
  const universe = page.getByLabel('Universe', { exact: true })
  const symbols = page.locator('#matches table.data tbody tr td.symbol')
  const ranks = page.locator('#matches table.data tbody tr td:first-child.num')

  // ALFA and ALFB are share classes of one company: together, under one number and name.
  await expect(symbols).toHaveText(['ALFA', 'ALFB', 'BRAVO', 'CHARL', 'DELTA'])
  await expect(ranks).toHaveText(['1', '2', '3', '4'])
  await expect(page.getByRole('cell', { name: 'Alfa Example Inc.' })).toHaveCount(1)

  await universe.selectOption('nasdaq')
  await expect(symbols).toHaveText(['ALFA', 'ALFB', 'CHARL'])

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

  // The portfolio open last follows the account, so the seeded one is chosen first.
  await openPortfolio(page, 1)
  await expect(page.getByLabel('Shares of ALFA')).toHaveValue('3.5')
  await page.getByRole('button', { name: 'Remove BRAVO' }).click()
  await expect(page.getByLabel('Shares of BRAVO')).toHaveCount(0)

  // Unsaved, so the holding is back after a reload.
  await page.reload()
  if (await load.isVisible().catch(() => false)) await load.click()
  await expect(page.getByLabel('Shares of BRAVO')).toBeVisible()
})

test('a holding set to 0 shares is removed when the portfolio is saved', async ({ page }) => {
  await page.goto('/portfolio')
  const load = page.getByRole('button', { name: 'Load', exact: true })
  if (await load.isVisible().catch(() => false)) await load.click()

  // A fifth portfolio, so the seeded one is left as the other tests expect it.
  await openPortfolio(page, 5)
  await page.getByRole('combobox', { name: 'Add a holding' }).fill('DELTA')
  await page.getByRole('option', { name: /DELTA/ }).first().click()
  await page.getByLabel('Shares of DELTA').fill('4')
  await page.getByRole('combobox', { name: 'Add a holding' }).fill('CHARL')
  await page.getByRole('option', { name: /CHARL/ }).first().click()
  await page.getByLabel('Shares of CHARL').fill('2')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled()

  await page.getByLabel('Shares of DELTA').fill('0')
  await expect(page.getByText('Saving removes DELTA, set to 0 shares.')).toBeVisible()
  const save = page.getByRole('button', { name: 'Save', exact: true })
  await expect(save).toBeEnabled()
  await save.click()

  await expect(page.getByLabel('Shares of DELTA')).toHaveCount(0)
  await page.reload()
  if (await load.isVisible().catch(() => false)) await load.click()
  await expect(page.getByLabel('Shares of CHARL')).toHaveValue('2')
  await expect(page.getByLabel('Shares of DELTA')).toHaveCount(0)
  await openPortfolio(page, 1)
})

test('a portfolio is set as a Roth IRA, and several are reviewed each with its own plan', async ({
  page,
}) => {
  await page.goto('/portfolio')
  const load = page.getByRole('button', { name: 'Load', exact: true })
  if (await load.isVisible().catch(() => false)) await load.click()

  // A second portfolio with a holding, so there are several to review together.
  await openPortfolio(page, 4)
  await page.getByRole('combobox', { name: 'Add a holding' }).fill('CHARL')
  await page.getByRole('option', { name: /CHARL/ }).first().click()
  await page.getByLabel('Shares of CHARL').fill('1')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled()

  const type = page.getByRole('combobox', { name: 'Account type' })
  await type.selectOption('roth_ira')
  const room = page.getByLabel('Room left to contribute this year, in dollars')
  await expect(room).toBeVisible()
  await room.fill('100')
  await room.blur()
  const holdings = page.locator('#holdings')
  await holdings.getByText(/Yearly contribution limits/).click()
  await expect(holdings.getByText(/These limits are for the 2026 and 2027 tax years/)).toBeVisible()
  await page.reload()
  if (await load.isVisible().catch(() => false)) await load.click()
  await expect(page.getByRole('combobox', { name: 'Account type' })).toHaveValue('roth_ira')
  await expect(page.getByLabel('Room left to contribute this year, in dollars')).toHaveValue('100')

  const review = page.locator('#ai-review')
  await review.getByRole('button', { name: 'Several together', exact: true }).click()
  await expect(review.getByRole('checkbox', { name: /Portfolio 1/ })).toBeChecked()
  await expect(review.getByRole('checkbox', { name: /Portfolio 4 · Roth IRA/ })).toBeChecked()
  await review.getByRole('button', { name: 'Each with its own plan', exact: true }).click()
  await review.getByRole('button', { name: 'Goals for each account', exact: true }).click()
  await review.getByText('Tailor the plan to your goals (optional)').click()
  await expect(review.getByLabel('Cash in Portfolio 1 to trade, in dollars')).toBeVisible()
  await expect(review.getByLabel('Horizon for Portfolio 4')).toBeVisible()
  await expect(review.getByText(/\$100 room left/)).toBeVisible()
  await expect(review.getByLabel('New cash to invest, in dollars')).toHaveCount(1)

  // Left as the other tests expect it.
  await review.getByRole('button', { name: 'This portfolio' }).click()
  await page.getByRole('combobox', { name: 'Account type' }).selectOption('')
  await page.getByRole('button', { name: 'Remove CHARL' }).click()
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled()
  await openPortfolio(page, 1)
})

test('Claude’s picks are shown, and the screener can list them alone', async ({ page }, info) => {
  test.skip(info.project.name === 'phone', 'The results table is checked at desktop width.')
  await page.goto('/screener/picks')

  await expect(page.getByText('Cheap growth, with a bond fund to steady it.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'BRAVO', exact: true })).toBeVisible()
  await expect(page.getByText(/Not in your screener/)).toBeVisible()

  await page.getByRole('link', { name: 'Show in the screener' }).click()
  await expect(page).toHaveURL(/\/screener\?picks=latest/)
  await expect(page.getByLabel('Universe', { exact: true })).toHaveValue('picks')
  // BNDX is not in the screener, so only the two stocks are listed.
  await expect(page.locator('#matches table.data tbody tr td.symbol')).toHaveText([
    'BRAVO',
    'CHARL',
  ])
})

test('saved picks are listed, and outside Claude is told it may search the web', async ({
  page,
}) => {
  await page.goto('/screener/picks')

  const saved = page.locator('#saved-picks')
  await expect(saved.getByRole('heading', { name: 'Saved Claude’s picks' })).toBeVisible()
  await expect(saved.locator('tbody tr')).toHaveCount(1)
  await expect(saved.getByRole('cell', { name: 'BRAVO, CHARL, BNDX' })).toBeVisible()
  // The test server has no price keys, so the lists stay listed without their returns.
  await expect(saved.getByText(/could not be priced/)).toBeVisible()
  await expect(page.locator('#picks-performance')).toBeVisible()

  const web = page.getByRole('checkbox', { name: /Let Claude search the web/ })
  await expect(web).toBeChecked()
  await page.getByText('Use Claude outside the app, without API costs').click()
  await expect(page.getByText(/One difference from the app: the web search/)).toBeVisible()
  await web.uncheck()
  await expect(page.getByText(/tells Claude to use only PyQxel’s data/)).toBeVisible()
  await web.check()
})

test('what is entered follows the account to another browser', async ({ page, browser }) => {
  await page.goto('/screener/picks')
  const saved = page.waitForResponse(
    (response) =>
      response.url().includes('/api/v1/me/saved') && response.request().method() === 'PUT',
  )
  await page.getByRole('textbox', { name: 'Anything else' }).fill('dividends, please')
  expect((await saved).ok()).toBe(true)

  // A new browser, with nothing stored but the sign-in.
  const other = await browser.newContext()
  const fresh = await other.newPage()
  await signIn(fresh)
  await fresh.goto('/screener/picks')
  await expect(fresh.getByRole('textbox', { name: 'Anything else' })).toHaveValue(
    'dividends, please',
  )
  // Left as the other tests expect it.
  const cleared = fresh.waitForResponse(
    (response) =>
      response.url().includes('/api/v1/me/saved') && response.request().method() === 'PUT',
  )
  await fresh.getByRole('textbox', { name: 'Anything else' }).fill('')
  await cleared
  await other.close()
})
