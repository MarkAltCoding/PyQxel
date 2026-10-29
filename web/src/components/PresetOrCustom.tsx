import { useEffect, useId, useRef, useState } from 'react'
import { Segmented } from './Segmented'

interface Props {
  label: string
  /** The usual choices, by value. */
  presets: readonly { value: number; label: string }[]
  value: number
  onChange: (value: number) => void
  /** The range a custom number may take, in the unit typed. */
  min: number
  max: number
  step?: number
  /** What a custom number counts, as its field is labeled: "days", "$ billions". */
  unit: string
  /** What one unit typed is worth in `value`: 1e9 when billions are typed for dollars. */
  scale?: number
}

/** How long a custom number waits after the last keystroke before it is used, so that
 * typing "45" does not ask for 4 first. */
const SETTLE_MS = 400

/** A row of preset choices with a Custom one that opens a number field, for filters whose
 * usual values are a few round numbers but any number in a range makes sense. A custom
 * number is passed on once it is within range and typing has paused. */
export function PresetOrCustom({
  label,
  presets,
  value,
  onChange,
  min,
  max,
  step = 1,
  unit,
  scale = 1,
}: Props) {
  const id = useId()
  const [custom, setCustom] = useState(() => !presets.some((preset) => preset.value === value))
  const [text, setText] = useState(() => String(value / scale))
  const changed = useRef(onChange)
  useEffect(() => {
    changed.current = onChange
  })

  const typed = Number(text)
  const valid = text.trim() !== '' && Number.isFinite(typed) && typed >= min && typed <= max
  useEffect(() => {
    if (!custom || !valid) return
    const timer = setTimeout(() => changed.current(typed * scale), SETTLE_MS)
    return () => clearTimeout(timer)
  }, [custom, valid, typed, scale])

  const options = [
    ...presets.map((preset) => ({ value: String(preset.value), label: preset.label })),
    { value: 'custom', label: 'Custom' },
  ]
  return (
    <div className="preset-or-custom">
      <Segmented
        label={label}
        value={custom ? 'custom' : String(value)}
        options={options}
        onChange={(choice) => {
          if (choice === 'custom') {
            setCustom(true)
            return
          }
          setCustom(false)
          setText(String(Number(choice) / scale))
          onChange(Number(choice))
        }}
      />
      {custom && (
        <span className="custom-number">
          <label htmlFor={id} className="visually-hidden">
            {`${label}, custom (${unit})`}
          </label>
          <input
            id={id}
            type="number"
            min={min}
            max={max}
            step={step}
            value={text}
            aria-invalid={!valid}
            aria-describedby={`${id}-hint`}
            onChange={(event) => setText(event.target.value)}
            onBlur={() => {
              if (valid && typed * scale !== value) onChange(typed * scale)
            }}
          />
          <small id={`${id}-hint`} className={valid ? 'muted' : 'down'}>
            {valid ? unit : `${min} to ${max} ${unit}`}
          </small>
        </span>
      )}
    </div>
  )
}
