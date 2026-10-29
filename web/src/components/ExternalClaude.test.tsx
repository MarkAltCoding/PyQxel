import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { ExternalClaude } from './ExternalClaude'

const PROMPT = { prompt: '## Instructions\n\nReview this.', filename: 'pyqxel-prompt.md' }

function open() {
  return userEvent.click(screen.getByText(/Use Claude outside the app/))
}

describe('ExternalClaude', () => {
  it('gets and copies the prompt, then saves a pasted answer', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const onPaste = vi.fn().mockResolvedValue(undefined)
    render(<ExternalClaude getPrompt={async () => PROMPT} onPaste={onPaste} />)
    await open()

    await userEvent.click(screen.getByRole('button', { name: 'Get the prompt' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Copy prompt' }))
    await userEvent.type(screen.getByLabelText('Claude’s answer'), '{{"headline": "x"}')
    await userEvent.click(screen.getByRole('button', { name: 'Save answer' }))

    expect(writeText).toHaveBeenCalledWith(PROMPT.prompt)
    expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download pyqxel-prompt.md' })).toBeInTheDocument()
    expect(onPaste).toHaveBeenCalledWith('{"headline": "x"}')
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
  })

  it('shows why an answer was refused, and keeps it to fix', async () => {
    const onPaste = vi
      .fn()
      .mockRejectedValue(new ApiError(422, 'The answer does not match the expected format.'))
    render(<ExternalClaude getPrompt={async () => PROMPT} onPaste={onPaste} />)
    await open()

    await userEvent.type(screen.getByLabelText('Claude’s answer'), 'not json')
    await userEvent.click(screen.getByRole('button', { name: 'Save answer' }))

    expect(await screen.findByText(/does not match the expected format/)).toBeInTheDocument()
    expect(screen.getByLabelText('Claude’s answer')).toHaveValue('not json')
  })

  it('shows problems found in the page plainly', async () => {
    render(
      <ExternalClaude
        getPrompt={async () => {
          throw new Error('Type your question in the box above first.')
        }}
        onPaste={vi.fn()}
      />,
    )
    await open()

    await userEvent.click(screen.getByRole('button', { name: 'Get the prompt' }))

    expect(
      await screen.findByText('Type your question in the box above first.'),
    ).toBeInTheDocument()
  })
})
