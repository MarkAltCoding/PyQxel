import { describe, expect, it } from 'vitest'
import { safeUrl } from './safeUrl'

describe('safeUrl', () => {
  it('keeps web addresses', () => {
    expect(safeUrl('https://www.sec.gov/x?y=1')).toBe('https://www.sec.gov/x?y=1')
    expect(safeUrl('http://example.com')).toBe('http://example.com/')
  })

  it.each([
    'javascript:alert(1)',
    ' JavaScript:alert(1)',
    'java\tscript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    '/relative/path',
    '',
    null,
  ])('refuses %s', (url) => {
    expect(safeUrl(url)).toBeUndefined()
  })
})
