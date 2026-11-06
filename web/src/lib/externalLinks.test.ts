import { describe, expect, it } from 'vitest'
import { describeHref, leavingHref } from './externalLinks'

const ORIGIN = 'tauri://localhost'

function link(href: string): HTMLElement {
  const anchor = document.createElement('a')
  anchor.setAttribute('href', href)
  const inner = document.createElement('span')
  anchor.append(inner)
  document.body.append(anchor)
  return inner
}

describe('links that leave PyQxel', () => {
  it('catches web, email and phone links, from anything inside them', () => {
    expect(leavingHref(link('https://finnhub.io/register'), ORIGIN)).toBe(
      'https://finnhub.io/register',
    )
    expect(leavingHref(link('mailto:help@example.com'), ORIGIN)).toBe('mailto:help@example.com')
    expect(leavingHref(link('tel:+15555550100'), ORIGIN)).toBe('tel:+15555550100')
  })

  it('leaves links within PyQxel and other schemes alone', () => {
    expect(leavingHref(link('/settings'), 'http://localhost:5173')).toBeNull()
    expect(leavingHref(link('http://localhost:5173/privacy'), 'http://localhost:5173')).toBeNull()
    expect(leavingHref(link('javascript:alert(1)'), ORIGIN)).toBeNull()
    expect(leavingHref(document.body, ORIGIN)).toBeNull()
  })

  it('names where a link goes', () => {
    expect(describeHref('https://www.sec.gov/edgar')).toBe('sec.gov')
    expect(describeHref('mailto:help@example.com')).toBe('help@example.com')
  })
})
