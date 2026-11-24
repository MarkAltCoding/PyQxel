import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AiOptions } from './AiOptions'

const noop = () => undefined

describe('AiOptions', () => {
  it('says what is chosen while folded', () => {
    const { rerender } = render(
      <AiOptions
        webSearch
        onWebSearch={noop}
        wait={300}
        onWait={noop}
        context=""
        onContext={noop}
      />,
    )
    expect(screen.getByText('Options').closest('details')).not.toHaveAttribute('open')
    expect(
      screen.getByText('Web search on · Waits up to 5 minutes for data · No added context'),
    ).toBeInTheDocument()

    rerender(
      <AiOptions
        webSearch={false}
        onWebSearch={noop}
        wait={0}
        onWait={noop}
        context="A note."
        onContext={noop}
      />,
    )
    expect(
      screen.getByText('Web search off · No waiting for data · 7 characters of context'),
    ).toBeInTheDocument()
  })
})
