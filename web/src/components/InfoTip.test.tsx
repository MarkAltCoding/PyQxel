import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { INFO, type InfoKey } from '../lib/glossary'
import { InfoTip } from './InfoTip'

const button = () => screen.getByRole('button', { name: 'About Sharpe ratio' })
const box = () => screen.queryByRole('tooltip')

describe('InfoTip', () => {
  it('opens on hover and closes once the cursor leaves the "i" and the box', async () => {
    render(<InfoTip topic="sharpe" />)
    expect(box()).toBeNull()

    await userEvent.hover(button())
    expect(box()).toHaveTextContent('Return earned per unit of risk taken')
    expect(box()).toHaveTextContent('Good: Above 1 is good')
    // Moving from the "i" into the box keeps it open: both are inside the same wrapper.
    await userEvent.hover(box()!)
    expect(box()).not.toBeNull()

    await userEvent.unhover(box()!)
    expect(box()).toBeNull()
  })

  it('stays open after a click until the "i" is clicked again', async () => {
    render(<InfoTip topic="sharpe" />)

    await userEvent.click(button())
    await userEvent.unhover(button())
    expect(box()).not.toBeNull()
    expect(button()).toHaveAttribute('aria-expanded', 'true')

    await userEvent.click(button())
    await userEvent.unhover(button())
    expect(box()).toBeNull()
  })

  it('closes a pinned box with Escape or a click elsewhere', async () => {
    render(
      <>
        <InfoTip topic="sharpe" />
        <p>Elsewhere</p>
      </>,
    )

    await userEvent.click(button())
    await userEvent.unhover(button())
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(box()).toBeNull()

    await userEvent.click(button())
    await userEvent.unhover(button())
    await userEvent.click(screen.getByText('Elsewhere'))
    expect(box()).toBeNull()
  })

  it('opens for keyboard users on focus', async () => {
    render(<InfoTip topic="sharpe" />)

    await userEvent.tab()

    expect(button()).toHaveFocus()
    expect(box()).not.toBeNull()
    expect(button()).toHaveAttribute('aria-describedby', box()!.id)
  })
})

describe('the glossary', () => {
  it('explains every entry in words', () => {
    for (const [key, entry] of Object.entries(INFO) as [InfoKey, (typeof INFO)[InfoKey]][]) {
      expect(entry.title.trim(), key).not.toBe('')
      expect(entry.what.length, key).toBeGreaterThan(20)
    }
  })
})
