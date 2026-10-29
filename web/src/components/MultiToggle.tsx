interface Props<T extends string> {
  label: string
  values: readonly T[]
  options: readonly { value: T; label: string }[]
  onChange: (values: T[]) => void
}

/** A row of buttons, each switched on and off by a click, for choices that combine, such
 * as which kinds of event to show. An "All" button switches every one on. The values
 * passed back keep the options' order. */
export function MultiToggle<T extends string>({ label, values, options, onChange }: Props<T>) {
  const chosen = new Set(values)
  const every = options.every((option) => chosen.has(option.value))
  const toggle = (value: T) =>
    onChange(
      options
        .map((option) => option.value)
        .filter((option) => (option === value ? !chosen.has(option) : chosen.has(option))),
    )
  return (
    <div className="segmented multi" role="group" aria-label={label}>
      <button
        type="button"
        aria-pressed={every}
        onClick={() => onChange(options.map((option) => option.value))}
      >
        All
      </button>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={chosen.has(option.value)}
          onClick={() => toggle(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
