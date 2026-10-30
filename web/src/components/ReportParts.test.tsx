import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ReportTabs, RiskCards, Summary } from './ReportParts'

describe('ReportTabs', () => {
  const tabs = [
    { id: 'a', label: 'Overview', content: <p>First panel</p> },
    { id: 'b', label: 'Risks', count: 3, content: <p>Second panel</p> },
    { id: 'c', label: 'Limits', content: <p>Third panel</p> },
  ]

  it('shows one panel at a time, chosen by click or arrow keys', async () => {
    render(<ReportTabs label="Parts" tabs={tabs} />)

    expect(screen.getByRole('tabpanel')).toHaveTextContent('First panel')
    expect(screen.getByRole('tab', { name: 'Risks 3' })).toHaveAttribute('aria-selected', 'false')

    await userEvent.click(screen.getByRole('tab', { name: 'Risks 3' }))
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Second panel')

    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: 'Limits' })).toHaveFocus()
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Third panel')

    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('First panel')
  })
})

describe('Summary', () => {
  it('shows the first paragraph until the rest is asked for', async () => {
    render(<Summary text={'One.\n\nTwo.\n\nThree.'} />)

    expect(screen.getByText('One.')).toBeInTheDocument()
    expect(screen.queryByText('Two.')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Read the full summary \(3/ }))
    expect(screen.getByText('Three.')).toBeInTheDocument()
  })
})

describe('RiskCards', () => {
  it('puts the most severe risks first', () => {
    render(
      <RiskCards
        risks={[
          { name: 'Mild', severity: 'low', evidence: 'a' },
          { name: 'Severe', severity: 'high', evidence: 'b' },
          { name: 'Middling', severity: 'elevated', evidence: 'c' },
        ]}
      />,
    )

    const names = screen
      .getAllByRole('listitem')
      .map((item) => item.querySelector('strong')?.textContent)
    expect(names).toEqual(['Severe', 'Middling', 'Mild'])
    expect(screen.getByRole('img', { name: 'Severity high, 4 of 4' })).toBeInTheDocument()
  })
})
