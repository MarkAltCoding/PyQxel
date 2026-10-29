import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { pageNumbers } from '../lib/paging'
import { MultiToggle } from './MultiToggle'
import { Pager } from './Pager'
import { PresetOrCustom } from './PresetOrCustom'

const KINDS = [
  { value: 'fomc', label: 'FOMC' },
  { value: 'earnings', label: 'Earnings' },
  { value: 'ipo', label: 'IPOs' },
] as const
type Kind = (typeof KINDS)[number]['value']

function Kinds({ initial }: { initial: Kind[] }) {
  const [values, setValues] = useState<Kind[]>(initial)
  return (
    <>
      <MultiToggle label="Events" values={values} options={KINDS} onChange={setValues} />
      <output>{values.join(',')}</output>
    </>
  )
}

const pressed = (name: string) => screen.getByRole('button', { name }).getAttribute('aria-pressed')

describe('MultiToggle', () => {
  it('switches each choice on and off, in the options’ order, and All selects every one', async () => {
    render(<Kinds initial={['fomc', 'earnings', 'ipo']} />)
    expect(pressed('All')).toBe('true')

    await userEvent.click(screen.getByRole('button', { name: 'Earnings' }))
    expect(screen.getByRole('status')).toHaveTextContent('fomc,ipo')
    expect(pressed('All')).toBe('false')
    expect(pressed('Earnings')).toBe('false')

    await userEvent.click(screen.getByRole('button', { name: 'FOMC' }))
    await userEvent.click(screen.getByRole('button', { name: 'IPOs' }))
    expect(screen.getByRole('status')).toHaveTextContent(/^$/)

    await userEvent.click(screen.getByRole('button', { name: 'IPOs' }))
    await userEvent.click(screen.getByRole('button', { name: 'Earnings' }))
    expect(screen.getByRole('status')).toHaveTextContent('earnings,ipo')

    await userEvent.click(screen.getByRole('button', { name: 'All' }))
    expect(screen.getByRole('status')).toHaveTextContent('fomc,earnings,ipo')
  })
})

describe('PresetOrCustom', () => {
  const presets = [
    { value: 10e9, label: '$10B+' },
    { value: 100e9, label: '$100B+' },
  ]

  it('passes presets on at once and a custom number, scaled, once typing pauses', async () => {
    const onChange = vi.fn()
    render(
      <PresetOrCustom
        label="Market cap"
        presets={presets}
        value={10e9}
        onChange={onChange}
        min={1}
        max={5000}
        unit="$ billions"
        scale={1e9}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: '$100B+' }))
    expect(onChange).toHaveBeenLastCalledWith(100e9)

    await userEvent.click(screen.getByRole('button', { name: 'Custom' }))
    const field = screen.getByRole('spinbutton', { name: 'Market cap, custom ($ billions)' })
    await userEvent.clear(field)
    await userEvent.type(field, '250')
    await vi.waitFor(() => expect(onChange).toHaveBeenLastCalledWith(250e9))
    expect(onChange).not.toHaveBeenCalledWith(2e9)
  })

  it('holds back a number out of range and says what range is allowed', async () => {
    const onChange = vi.fn()
    render(
      <PresetOrCustom
        label="Window"
        presets={[{ value: 30, label: '30 days' }]}
        value={45}
        onChange={onChange}
        min={1}
        max={90}
        unit="days"
      />,
    )
    // A value that is no preset opens on Custom.
    const field = screen.getByRole('spinbutton', { name: 'Window, custom (days)' })
    expect(field).toHaveValue(45)

    await userEvent.clear(field)
    await userEvent.type(field, '120')
    await userEvent.tab()

    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('1 to 90 days')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('Pager', () => {
  it('shows the first, last and nearby pages, eliding the rest', () => {
    expect(pageNumbers(1, 3)).toEqual([1, 2, 3])
    expect(pageNumbers(6, 12)).toEqual([1, null, 4, 5, 6, 7, 8, null, 12])
    expect(pageNumbers(1, 12)).toEqual([1, 2, 3, null, 12])
  })

  it('moves between pages and changes how many each shows', async () => {
    const onPage = vi.fn()
    const onPageSize = vi.fn()
    render(
      <Pager
        page={1}
        pageSize={10}
        total={35}
        onPage={onPage}
        onPageSize={onPageSize}
        noun="headlines"
      />,
    )

    expect(screen.getByText('1–10 of 35 headlines')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Page 1' })).toHaveAttribute('aria-current', 'page')
    await userEvent.click(screen.getByRole('button', { name: 'Page 4' }))
    expect(onPage).toHaveBeenLastCalledWith(4)
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(onPage).toHaveBeenLastCalledWith(2)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Per page' }), '50')
    expect(onPageSize).toHaveBeenCalledWith(50)
  })
})
