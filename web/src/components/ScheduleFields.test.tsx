import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RefreshSchedule } from '../api/types'
import { DEFAULT_SCHEDULE } from '../lib/schedule'
import { ScheduleFields } from './ScheduleFields'

let latest: RefreshSchedule = DEFAULT_SCHEDULE

function Harness() {
  const [schedule, setSchedule] = useState<RefreshSchedule>(DEFAULT_SCHEDULE)
  return (
    <ScheduleFields
      schedule={schedule}
      onChange={(next) => {
        latest = next
        setSchedule(next)
      }}
      baseTime="19:00"
    />
  )
}

describe('ScheduleFields', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 10, 20, 12))
  })
  afterEach(() => vi.useRealTimers())

  it('chooses days on a calendar of this month and the next, past days closed', async () => {
    render(<Harness />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'How often' }), 'dates')
    await userEvent.click(screen.getByRole('button', { name: 'Choose dates' }))

    const dialog = screen.getByRole('dialog', { name: 'Choose the days to refresh' })
    expect(within(dialog).getByRole('group', { name: 'November 2026' })).toBeInTheDocument()
    expect(within(dialog).getByRole('group', { name: 'December 2026' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Thursday, November 19' })).toBeDisabled()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Wednesday, November 25' }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Thursday, December 31' }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Use 2 days' }))

    expect(latest.dates).toEqual(['2026-11-25', '2026-12-31'])
    expect(screen.getByRole('button', { name: '2 days chosen: change' })).toBeInTheDocument()
  })

  it('gives some days of the week their own time', async () => {
    render(<Harness />)
    await userEvent.click(screen.getByText(/A different time on some days/))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Saturday' }))

    expect(latest.times).toEqual({ 5: '19:00' })
    expect(screen.getByLabelText('Time on Saturday')).toHaveValue('19:00')
  })
})
