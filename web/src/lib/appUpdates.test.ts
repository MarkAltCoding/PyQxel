import { describe, expect, it } from 'vitest'
import { summarizeNotes } from './appUpdates'

describe('summarizeNotes', () => {
  it('keeps the first lines as plain text', () => {
    expect(summarizeNotes('Faster backtests\nA new chart\nFix sign-in\nMore')).toBe(
      'Faster backtests\nA new chart\nFix sign-in',
    )
  })

  it('leaves out Markdown headings, list markers, and changelog links', () => {
    const body = [
      "## What's Changed",
      '* Faster backtests',
      '- A new chart',
      '',
      '**Full Changelog**: https://github.com/MarkAltCoding/PyQxel/compare/v0.1.0...v0.2.0',
    ].join('\n')
    expect(summarizeNotes(body)).toBe('Faster backtests\nA new chart')
  })

  it('has nothing to show for empty notes', () => {
    expect(summarizeNotes(undefined)).toBeNull()
    expect(summarizeNotes("## What's Changed\n")).toBeNull()
  })
})
