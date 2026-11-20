import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WaitingNote } from './WaitingNote'

describe('WaitingNote', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('explains a quiet wait after 20 seconds, and only when the person chose to wait', () => {
    const { rerender } = render(<WaitingNote wait={300} quiet />)
    act(() => vi.advanceTimersByTime(10_000))
    expect(screen.queryByRole('status')).toBeNull()

    act(() => vi.advanceTimersByTime(65_000))
    expect(screen.getByRole('status')).toHaveTextContent(
      /Still gathering data \(1 min 15 s so far\).*up to the 5 minutes you chose/,
    )

    rerender(<WaitingNote wait={300} quiet={false} />)
    expect(screen.queryByRole('status')).toBeNull()
    rerender(<WaitingNote wait={0} quiet />)
    act(() => vi.advanceTimersByTime(60_000))
    expect(screen.queryByRole('status')).toBeNull()
  })
})
