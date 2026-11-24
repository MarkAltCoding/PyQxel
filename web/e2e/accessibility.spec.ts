// Every page passes axe's checks for WCAG 2.1 A and AA (contrast, labels, alt text, names,
// landmarks), in the light theme and the dark one.
import AxeBuilder from '@axe-core/playwright'
import { expect, signIn, test } from './fixtures'

const PAGES = [
  '/',
  '/markets',
  '/portfolio',
  '/backtest',
  '/screener',
  '/picks',
  '/screener/refresh',
  '/settings',
  '/privacy',
  '/terms',
  '/refunds',
]

for (const theme of ['light', 'dark'] as const) {
  test.describe(`the ${theme} theme`, () => {
    test.use({ colorScheme: theme })

    test('the sign-in page is accessible', async ({ page }) => {
      await page.addInitScript((chosen) => localStorage.setItem('pyqxel.theme', chosen), theme)
      await page.goto('/signin')
      await expect(page.getByRole('button', { name: 'Sign in with key' })).toBeVisible()
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
        .analyze()
      expect(results.violations.map(describe)).toEqual([])
    })

    for (const path of PAGES) {
      test(`${path} is accessible`, async ({ page }) => {
        await signIn(page)
        await page.addInitScript((chosen) => localStorage.setItem('pyqxel.theme', chosen), theme)
        await page.goto(path)
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
        await page.waitForLoadState('networkidle')
        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
          // Charts are canvases, described by their cards' text and "i" tips.
          .exclude('canvas')
          .analyze()
        expect(results.violations.map(describe)).toEqual([])
      })
    }
  })
}

/** A violation as one readable line, with where it happened. */
function describe(violation: {
  id: string
  help: string
  nodes: { target: unknown[]; failureSummary?: string }[]
}) {
  const where = violation.nodes
    .slice(0, 4)
    .map((node) => {
      const why = node.failureSummary?.split('\n')[1]?.trim()
      return why ? `${node.target.join(' ')} (${why})` : node.target.join(' ')
    })
    .join(' | ')
  return `${violation.id}: ${violation.help} — ${where}`
}
