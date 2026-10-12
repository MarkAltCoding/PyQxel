import { useEffect, useId, useState, type KeyboardEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { search } from '../api/endpoints'
import type { SearchMatch } from '../api/types'
import { compact } from '../lib/format'

const SYMBOL = /^[A-Za-z0-9.\-^=]{1,15}$/

interface Props {
  onSelect: (symbol: string) => void
  placeholder?: string
  large?: boolean
  label?: string
  /** Clears the box after a choice, as when adding holdings one after another. */
  clearOnSelect?: boolean
  autoFocus?: boolean
}

/**
 * A combobox over the screened universe. Any valid ticker can also be entered directly,
 * since ETFs and foreign listings are outside the universe but work everywhere else.
 */
export function SearchBox({
  onSelect,
  placeholder = 'Search by ticker or company',
  large,
  label = 'Search stocks',
  clearOnSelect,
  autoFocus,
}: Props) {
  const id = useId()
  const [text, setText] = useState('')
  const [debounced, setDebounced] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(text.trim()), 150)
    return () => clearTimeout(timer)
  }, [text])

  const { data } = useQuery({
    queryKey: ['search', debounced],
    queryFn: ({ signal }) => search(debounced, signal),
    enabled: debounced.length > 0,
    staleTime: 5 * 60_000,
  })

  const typed = text.trim().toUpperCase()
  const matches: SearchMatch[] = data?.query === debounced ? data.items : []
  const exact = matches.some((match) => match.symbol === typed)
  const options: { symbol: string; match?: SearchMatch }[] = [
    ...matches.map((match) => ({ symbol: match.symbol, match })),
    ...(SYMBOL.test(typed) && !exact ? [{ symbol: typed }] : []),
  ]
  const shown = open && text.trim().length > 0 && options.length > 0

  const choose = (symbol: string) => {
    onSelect(symbol)
    setOpen(false)
    setText(clearOnSelect ? '' : symbol)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setOpen(true)
      setActive((index) => Math.min(index + 1, options.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((index) => Math.max(index - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const option = options[active] ?? (SYMBOL.test(typed) ? { symbol: typed } : undefined)
      if (option) choose(option.symbol)
    } else if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className={`search${large ? ' search-large' : ''}`}>
      <label htmlFor={`${id}-input`} className="visually-hidden">
        {label}
      </label>
      <input
        id={`${id}-input`}
        type="search"
        role="combobox"
        aria-expanded={shown}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        aria-activedescendant={shown ? `${id}-option-${active}` : undefined}
        autoComplete="off"
        spellCheck={false}
        autoFocus={autoFocus}
        placeholder={placeholder}
        value={text}
        onChange={(event) => {
          setText(event.target.value)
          setActive(0)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {shown && (
        <ul id={`${id}-list`} role="listbox" className="search-list">
          {options.map((option, index) => (
            <li
              key={option.symbol}
              id={`${id}-option-${index}`}
              role="option"
              aria-selected={index === active}
              onMouseEnter={() => setActive(index)}
              // Before the input's blur closes the list.
              onMouseDown={(event) => {
                event.preventDefault()
                choose(option.symbol)
              }}
            >
              <span className="symbol">{option.symbol}</span>
              {option.match ? (
                <>
                  <span className="name">{option.match.name}</span>
                  <span className="meta">{compact(option.match.market_cap)}</span>
                </>
              ) : (
                <span className="name">Go to {option.symbol}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
