import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ProviderUsage } from '../api/types'
import { KeyUsage } from './KeyUsage'

const TIINGO: ProviderUsage = {
  provider: 'tiingo',
  name: 'Tiingo',
  plan: 'free',
  plan_name: 'Starter (free)',
  plan_detected: false,
  plans: [
    { id: 'free', name: 'Starter (free)', verified: true },
    { id: 'power', name: 'Power', verified: true },
  ],
  source: 'pyqxel',
  limits: [
    {
      window: 'hour',
      unit: 'requests',
      limit: 50,
      used: 47,
      remaining: 3,
      resets: 'Every hour',
      resets_at: '2026-11-05T21:00:00Z',
      source: 'pyqxel',
    },
    {
      window: 'month',
      unit: 'bytes',
      limit: 1024 ** 3,
      used: 50 * 1024 ** 2,
      remaining: 1024 ** 3 - 50 * 1024 ** 2,
      resets: 'The 1st of each month, at midnight US Eastern Standard Time',
      source: 'pyqxel',
    },
  ],
  note: "Tiingo does not report usage, so these figures count only PyQxel's own requests.",
}

describe('KeyUsage', () => {
  it('shows each limit, what is used and left, who counted, and the plan', async () => {
    const onPlan = vi.fn()
    render(<KeyUsage report={TIINGO} onPlan={onPlan} />)

    const rows = screen.getAllByRole('row').slice(1)
    expect(within(rows[0]!).getByText('50 requests an hour')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('47')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('3')).toHaveClass('down')
    expect(within(rows[0]!).getByText('PyQxel’s count')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('1.0 GB a month')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('50 MB')).toBeInTheDocument()
    expect(screen.getByText(/count only PyQxel's own requests/)).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Plan' }), 'power')
    expect(onPlan).toHaveBeenCalledWith('power')
  })
})
