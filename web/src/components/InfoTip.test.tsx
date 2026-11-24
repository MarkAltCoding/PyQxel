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

  it('keeps what shows at a glance short, with longer detail under More', () => {
    for (const [key, entry] of Object.entries(INFO) as [InfoKey, (typeof INFO)[InfoKey]][]) {
      expect(entry.what.length, key).toBeLessThanOrEqual(200)
      expect((entry.good ?? '').length, key).toBeLessThanOrEqual(200)
      expect((entry.bad ?? '').length, key).toBeLessThanOrEqual(200)
      expect((entry.note ?? '').length, key).toBeLessThanOrEqual(160)
    }
  })
})

describe('the explanation box', () => {
  it('sets a formula with its subscripts, and keeps the detail behind More', async () => {
    render(<InfoTip topic="sharpe" />)
    await userEvent.hover(button())

    const formula = box()!.querySelector('.info-tip-formula')!
    expect(formula).toHaveTextContent('Sharpe = mean(r − rf) ÷ stdev(r) × √252')
    expect(formula.querySelector('sub')).toHaveTextContent('f')
  })

  it('shows the detail on More, and all of it when pinned', async () => {
    render(<InfoTip topic="webSearch" />)
    const tip = () => screen.getByRole('button', { name: 'About Let Claude search the web' })

    await userEvent.hover(tip())
    expect(box()).not.toHaveTextContent('about 1 cent per search')
    await userEvent.click(screen.getByRole('button', { name: 'More' }))
    expect(box()).toHaveTextContent('about 1 cent per search')

    await userEvent.unhover(tip())
    await userEvent.click(tip())
    expect(box()).toHaveTextContent('about 1 cent per search')
    expect(screen.queryByRole('button', { name: 'More' })).not.toBeInTheDocument()
  })
})
