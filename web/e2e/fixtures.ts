// Shared setup for the browser tests: the seeded account's key, a page that fails the test
// on any uncaught error, and a page already signed in.
import { readFileSync } from 'node:fs'
import { test as base, expect, type Page } from '@playwright/test'

/** The test account's API key, written by scripts/e2e_seed.py when the server starts. */
export function apiKey(): string {
  return readFileSync(new URL('./.data/api-key', import.meta.url), 'utf8').trim()
}

/** Signs `page` in on every load, past the advice notice shown on first use. */
export async function signIn(page: Page): Promise<void> {
  await page.addInitScript((key) => {
    localStorage.setItem('pyqxel.apiKey', key)
    localStorage.setItem('pyqxel.adviceNoticeAccepted', '2026-11-11T00:00:00Z')
  }, apiKey())
}

/** How far the page is wider than the window: above zero, it scrolls sideways. */
export function overflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
}

export const test = base.extend({
  page: async ({ page }, use) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await use(page)
    expect(errors, 'errors thrown in the page').toEqual([])
  },
})

export { expect }
