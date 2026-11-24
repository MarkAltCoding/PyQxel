import { useId, useState, type ReactNode } from 'react'

/**
 * A short explanation with the rest behind "Read more": what something does stays in view,
 * and the detail most people never need (limits, fees, edge cases) is one click away.
 */
export function ReadMore({
  children,
  more,
  className,
}: {
  /** What everyone should read: one or two short sentences. */
  children: ReactNode
  /** The detail, shown in place when asked for. */
  more: ReactNode
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const id = useId()
  return (
    <div className={`read-more${className ? ` ${className}` : ''}`}>
      <p>
        {children}{' '}
        {!open && (
          <button
            type="button"
            className="read-more-toggle"
            aria-expanded={false}
            aria-controls={id}
            onClick={() => setOpen(true)}
          >
            Read more
          </button>
        )}
      </p>
      <div id={id} className="read-more-body" hidden={!open}>
        {more}{' '}
        <button
          type="button"
          className="read-more-toggle"
          aria-expanded={true}
          aria-controls={id}
          onClick={() => setOpen(false)}
        >
          Show less
        </button>
      </div>
    </div>
  )
}
