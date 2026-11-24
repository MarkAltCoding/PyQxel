import { useId } from 'react'
import { MAX_ADDITIONAL_CONTEXT } from '../lib/additionalContext'
import { InfoTip } from './InfoTip'

/** A large box for material Claude should weigh beside the data, apart from the notes. */
export function AdditionalContextField({
  value,
  onChange,
  disabled = false,
}: {
  value: string
  onChange: (text: string) => void
  disabled?: boolean
}) {
  const id = useId()
  return (
    <div className="field additional-context">
      <span className="heading-with-info">
        <label htmlFor={id}>Additional context (optional)</label>
        <InfoTip topic="additionalContext" />
      </span>
      <textarea
        id={id}
        rows={5}
        value={value}
        disabled={disabled}
        maxLength={MAX_ADDITIONAL_CONTEXT}
        placeholder="Paste articles, research, or notes"
        onChange={(event) => onChange(event.target.value)}
      />
      <span className="disclaimer">
        {value.length.toLocaleString('en-US')} of {MAX_ADDITIONAL_CONTEXT.toLocaleString('en-US')}{' '}
        characters
        {value && (
          <>
            {' · '}
            <button
              type="button"
              className="link-button"
              disabled={disabled}
              onClick={() => onChange('')}
            >
              Clear
            </button>
          </>
        )}
      </span>
    </div>
  )
}
