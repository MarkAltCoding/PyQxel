import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { StrategyAssistant } from './StrategyAssistant'

const CODE = 'def strategy(bars):\n    return bars["Close"].rolling(5).mean()\n'
const FIXED = CODE.replace('.mean()', '.mean().fillna(0.0)')

const sse = (...events: [string, unknown][]) =>
  new Response(
    events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''),
  )

afterEach(() => vi.restoreAllMocks())

describe('StrategyAssistant', () => {
  it('sends the code and the last run with the question, and applies the revision', async () => {
    const bodies: Record<string, unknown>[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      bodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>)
      return sse(
        ['thinking', { channel: 'thinking', text: 'Reading the traceback.' }],
        [
          'result',
          {
            reply: { explanation: 'Rolling means are NaN until they fill.', code: FIXED },
            model: 'claude-opus-5-5',
          },
        ],
      )
    })
    const onApply = vi.fn()
    const lastRun = { error_kind: 'result', message: 'The position is NaN.', line: 2 }
    render(
      <StrategyAssistant
        language="python"
        code={CODE}
        symbol="SPY"
        interval="1d"
        lastRun={lastRun}
        onApply={onApply}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Fix the error from my last run.' }))

    expect(await screen.findByText('Rolling means are NaN until they fill.')).toBeInTheDocument()
    expect(bodies[0]).toMatchObject({
      language: 'python',
      code: CODE,
      message: 'Fix the error from my last run.',
      symbol: 'SPY',
      interval: '1d',
      last_run: lastRun,
      history: [],
    })
    await userEvent.click(screen.getByRole('button', { name: 'Apply to editor' }))
    expect(onApply).toHaveBeenCalledWith(FIXED)
    expect(screen.getByRole('button', { name: 'Applied' })).toBeDisabled()
  })

  it('carries the conversation, with the code it proposed, into the next question', async () => {
    const bodies: { history: { role: string; content: string }[] }[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      bodies.push(JSON.parse(String(init?.body)) as (typeof bodies)[number])
      return sse([
        'result',
        { reply: { explanation: `Answer ${bodies.length}.`, code: FIXED }, model: 'm' },
      ])
    })
    render(
      <StrategyAssistant
        language="python"
        code={CODE}
        symbol=""
        interval="1d"
        lastRun={null}
        onApply={vi.fn()}
      />,
    )

    await userEvent.type(screen.getByLabelText('Question for Claude'), 'Add a stop.{Enter}')
    await screen.findByText('Answer 1.')
    await userEvent.type(screen.getByLabelText('Question for Claude'), 'Tighten it.{Enter}')
    await screen.findByText('Answer 2.')

    const [user, assistant] = bodies[1]?.history ?? []
    expect([user?.role, assistant?.role]).toEqual(['user', 'assistant'])
    expect(assistant?.content).toContain(FIXED)
  })

  it('shows the error the stream reports', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sse(['error', { status: 503, detail: 'The AI service is rate limited.', retry_after: 9 }]),
    )
    render(
      <StrategyAssistant
        language="cpp"
        code="void strategy();"
        symbol="SPY"
        interval="1d"
        lastRun={null}
        onApply={vi.fn()}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Explain what this strategy does.' }))

    expect(await screen.findByText(/rate limited/)).toBeInTheDocument()
  })
})
