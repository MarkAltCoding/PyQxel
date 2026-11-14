import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AiProgressDetail, WebSearchOption, WebSources } from './WebSearch'

const RESEARCH = {
  searched_at: '2026-11-13T12:00:00Z',
  queries: ['apple earnings'],
  searches: 2,
  findings: [
    {
      id: 1,
      text: 'Apple reported record revenue.',
      sources: [
        {
          url: 'https://www.reuters.com/apple',
          title: 'Apple posts record',
          cited_text: 'Apple said revenue rose.',
        },
        { url: 'https://apnews.com/apple', title: null, cited_text: null },
      ],
    },
    { id: 2, text: 'A doubtful claim.', sources: [{ url: 'javascript:alert(1)', title: 'Bad' }] },
  ],
}

describe('WebSearchOption', () => {
  it('offers the search with its extra cost beside it, and explains it', async () => {
    const onChange = vi.fn()
    render(<WebSearchOption checked={false} onChange={onChange} />)

    const box = screen.getByRole('checkbox', { name: /Let Claude search the web/ })
    expect(box).not.toBeChecked()
    expect(screen.getByText('(a few cents more)')).toBeInTheDocument()
    await userEvent.click(box)
    expect(onChange).toHaveBeenCalledWith(true)

    await userEvent.click(screen.getByRole('button', { name: /Let Claude search the web/ }))
    expect(await screen.findByText(/about 1 cent per search/)).toBeInTheDocument()
  })
})

describe('WebSources', () => {
  it('shows nothing when the web was not searched', () => {
    const { container } = render(<WebSources research={null} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('lists every fact with the pages it came from, under the number the report cites', () => {
    render(<WebSources research={RESEARCH} />)

    const list = screen.getByRole('list')
    const [first, second] = within(list).getAllByRole('listitem')
    expect(first).toHaveAttribute('value', '1')
    expect(first).toHaveTextContent('Apple reported record revenue.')
    const reuters = within(first!).getByRole('link', { name: 'Apple posts record (reuters.com)' })
    expect(reuters).toHaveAttribute('href', 'https://www.reuters.com/apple')
    expect(reuters).toHaveAttribute('target', '_blank')
    expect(reuters).toHaveAttribute('rel', 'noopener noreferrer')
    expect(within(first!).getByRole('link', { name: 'apnews.com' })).toBeInTheDocument()
    // Only web addresses become links.
    expect(within(second!).queryByRole('link')).toBeNull()
    expect(screen.getByText(/2 searches/)).toBeInTheDocument()
  })

  it('says so when the search found nothing it could tie to a page', () => {
    render(<WebSources research={{ ...RESEARCH, findings: [], searches: 1 }} />)

    expect(screen.getByText(/nothing here comes from the web/)).toBeInTheDocument()
    expect(screen.getByText(/1 search(?!es)/)).toBeInTheDocument()
  })
})

describe('AiProgressDetail', () => {
  it('shows each search as it is made, then the reasoning', () => {
    render(
      <AiProgressDetail
        progress={{ thinking: 'Weighing momentum.', characters: 0, searches: ['fed rates'] }}
      />,
    )

    expect(screen.getByText('Searched the web for “fed rates”')).toBeInTheDocument()
    expect(screen.getByText('Weighing momentum.')).toBeInTheDocument()
  })
})
