import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { FullText, TextPoints } from './ReportParts'

describe('a summary paragraph that ends in a list', () => {
  it('shows the lines as list items after the lead sentence', () => {
    render(
      <FullText
        text={
          'Balance-sheet risk is growing:\n- Commitments jumped.\n- Guarantees rose,\n  sharply.\n\nNext paragraph.'
        }
      />,
    )

    expect(screen.getByText('Balance-sheet risk is growing:').tagName).toBe('P')
    expect(screen.getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Commitments jumped.',
      'Guarantees rose, sharply.',
    ])
    expect(screen.getByText('Next paragraph.').tagName).toBe('P')
  })

  it('leaves a paragraph without a list as one paragraph', () => {
    render(<FullText text={'A dash - inside a sentence stays.'} />)
    expect(screen.queryByRole('list')).toBeNull()
  })
})

describe('a report point', () => {
  it('drops bold marks and splits a list run into one line', () => {
    render(
      <TextPoints
        title="Supporting points"
        items={['**Margins** (10-Q): - Gross 63.2% vs 61.5%. - Operating income +13%.']}
      />,
    )

    expect(screen.getByText('Margins (10-Q):').tagName).toBe('P')
    const inner = screen.getAllByRole('list')[1]!
    expect([...inner.querySelectorAll('li')].map((item) => item.textContent)).toEqual([
      'Gross 63.2% vs 61.5%.',
      'Operating income +13%.',
    ])
  })

  it('leaves a dash inside a sentence alone', () => {
    render(<TextPoints title="Points" items={['Revenue rose - slowly - this year.']} />)
    expect(screen.getAllByRole('list')).toHaveLength(1)
    expect(screen.getByText('Revenue rose - slowly - this year.')).toBeTruthy()
  })
})

describe('code in Claude’s prose', () => {
  it('sets a backticked snippet as code and keeps the words around it', () => {
    render(<FullText text={'Scale by `0.15 / volatility` each day.'} />)
    expect(screen.getByText('0.15 / volatility').tagName).toBe('CODE')
    expect(screen.getByText(/each day\./)).toBeTruthy()
  })
})
