import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { INFO, type InfoKey } from '../lib/glossary'

/**
 * A small "i" beside a stat, model or chart that explains it. Hovering opens the
 * explanation until the cursor leaves both the "i" and the box; clicking pins it open
 * until the "i" is clicked again. Keyboard focus opens it too, and Escape closes it.
 *
 * The box is drawn at the end of the page, placed under its "i", so a table or card that
 * scrolls sideways neither cuts it off nor grows a scrollbar to make room for it.
 */
export function InfoTip({ topic }: { topic: InfoKey }) {
  const entry = INFO[topic]
  const id = useId()
  const [hovered, setHovered] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null)
  const wrapper = useRef<HTMLSpanElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const open = hovered || pinned

  // Placed under the "i", in page coordinates, and kept inside the window: a box that would
  // run off the right edge opens leftward. It follows the "i" when anything scrolls.
  useLayoutEffect(() => {
    if (!open) return
    const position = () => {
      if (!box.current || !wrapper.current) return
      const anchor = wrapper.current.getBoundingClientRect()
      const width = box.current.offsetWidth
      const left =
        anchor.left + width > window.innerWidth - 8
          ? Math.max(8, anchor.right - width)
          : anchor.left
      setPlace({ top: anchor.bottom + window.scrollY, left: left + window.scrollX })
    }
    position()
    window.addEventListener('resize', position)
    window.addEventListener('scroll', position, true)
    return () => {
      window.removeEventListener('resize', position)
      window.removeEventListener('scroll', position, true)
    }
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
      const target = event.target as Node
      if (pinned && !wrapper.current?.contains(target) && !box.current?.contains(target)) {
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
      {open &&
        createPortal(
          <div
            ref={box}
            id={id}
            role="tooltip"
            className="info-tip-box"
            style={
              place
                ? { top: place.top, left: place.left }
                : { top: 0, left: 0, visibility: 'hidden' }
            }
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
          </div>,
          document.body,
        )}
    </span>
  )
}
