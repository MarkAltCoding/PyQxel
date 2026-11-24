import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ReadMore } from './ReadMore'

describe('ReadMore', () => {
  it('shows the summary, and the detail only when asked', async () => {
    render(<ReadMore more="The fine print.">What it does.</ReadMore>)

    expect(screen.getByText('What it does.')).toBeVisible()
    expect(screen.getByText('The fine print.')).not.toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Read more' }))
    expect(screen.getByText('The fine print.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Read more' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Show less' }))
    expect(screen.getByText('The fine print.')).not.toBeVisible()
  })
})
