import { WAIT_CHOICES } from '../lib/waitForData'
import { InfoTip } from './InfoTip'

/** How long to wait for more data where a provider's per-minute limit holds it back. */
export function WaitForDataOption({
  seconds,
  onChange,
  disabled = false,
}: {
  seconds: number
  onChange: (seconds: number) => void
  disabled?: boolean
}) {
  return (
    <span className="heading-with-info wait-for-data-option">
      <label className="field inline">
        <span>Wait for more data</span>
        <select
          value={seconds}
          disabled={disabled}
          onChange={(event) => onChange(Number(event.target.value))}
        >
          {WAIT_CHOICES.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </select>
      </label>
      <InfoTip topic="waitForData" />
    </span>
  )
}
