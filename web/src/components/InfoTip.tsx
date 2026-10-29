import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { INFO, type InfoKey } from '../lib/glossary'

/**
 * A small "i" beside a stat, model or chart that explains it. Hovering opens the
 * explanation until the cursor leaves both the "i" and the box; clicking pins it open
 * until the "i" is clicked again. Keyboard focus opens it too, and Escape closes it.
 */
export function InfoTip({ topic }: { topic: InfoKey }) {
  const entry = INFO[topic]
  const id = useId()
  const [hovered, setHovered] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [alignRight, setAlignRight] = useState(false)
  const wrapper = useRef<HTMLSpanElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const open = hovered || pinned

  // Kept inside the window: a box that would run off the right edge opens leftward.
  useLayoutEffect(() => {
    if (!open || !box.current || !wrapper.current) return
    const width = box.current.offsetWidth
    const left = wrapper.current.getBoundingClientRect().left
    setAlignRight(left + width > window.innerWidth - 8)
  }, [open])

  // A pinned box closes with Escape, or a click anywhere outside it.
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setPinned(false)
        setHovered(false)
      }
    }
    const onClick = (event: MouseEvent) => {
      if (pinned && wrapper.current && !wrapper.current.contains(event.target as Node)) {
        setPinned(false)
      }
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [open, pinned])

  return (
    <span
      ref={wrapper}
      className="info-tip"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        type="button"
        className={`info-tip-button${pinned ? ' pinned' : ''}`}
        aria-label={`About ${entry.title}`}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onClick={(event) => {
          event.stopPropagation()
          setPinned((current) => !current)
        }}
        onFocus={() => setHovered(true)}
        onBlur={() => setHovered(false)}
      >
        i
      </button>
      {open && (
        <div
          ref={box}
          id={id}
          role="tooltip"
          className={`info-tip-box${alignRight ? ' align-right' : ''}`}
        >
          <strong>{entry.title}</strong>
          <p>{entry.what}</p>
          {entry.good && (
            <p>
              <span className="info-tip-good">Good:</span> {entry.good}
            </p>
          )}
          {entry.bad && (
            <p>
              <span className="info-tip-bad">Watch for:</span> {entry.bad}
            </p>
          )}
          {entry.note && <p className="info-tip-note">{entry.note}</p>}
        </div>
      )}
    </span>
  )
}
