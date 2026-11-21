import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getFeatureUsage } from '../api/endpoints'
import { KEY_FEATURES, storedKeyRules, storeKeyRule, type KeyFeature } from '../lib/keyFeatures'
import type { ProviderName } from '../lib/providerKeys'
import { InfoTip } from './InfoTip'

/**
 * A dropdown under a key in Settings: each part of the app that can use the key, with a
 * checkbox to let it (on by default) and a daily limit on its requests, beside what it has
 * used today.
 */
export function KeyFeatures({ provider, name }: { provider: ProviderName; name: string }) {
  const [open, setOpen] = useState(false)
  const [rules, setRules] = useState(storedKeyRules)
  const used = useQuery({
    queryKey: ['feature-usage'],
    queryFn: getFeatureUsage,
    enabled: open,
    staleTime: 30_000,
  })
  const features = KEY_FEATURES.filter((feature) => feature.uses.includes(provider))
  const change = (feature: KeyFeature, next: { off?: boolean; daily?: number }) => {
    storeKeyRule(provider, feature, next)
    setRules(storedKeyRules())
  }
  const restricted = features.filter((feature) => rules[provider]?.[feature.value]).length
  return (
    <details
      className="key-features"
      onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
    >
      <summary>
        Where PyQxel may use this key
        {restricted > 0 ? ` (${restricted} changed)` : ''}
      </summary>
      <div className="stack" style={{ gap: 6, marginTop: 8 }}>
        <div className="heading-with-info">
          <span className="disclaimer">
            Untick a part to keep it from using your {name} key, or cap its requests a day.
          </span>
          <InfoTip topic="keyFeatures" />
        </div>
        {features.map((feature) => {
          const rule = rules[provider]?.[feature.value] ?? {}
          const today = used.data?.used?.[provider]?.[feature.value] ?? 0
          return (
            <div key={feature.value} className="key-feature-row">
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={!rule.off}
                  onChange={(event) =>
                    change(feature.value, { ...rule, off: !event.target.checked })
                  }
                />
                {feature.label}
              </label>
              <label className="field key-feature-limit">
                <span className="visually-hidden">
                  Daily limit for {feature.label} on your {name} key
                </span>
                <input
                  type="number"
                  inputMode="numeric"
                  min={0}
                  step={1}
                  placeholder="No limit"
                  disabled={rule.off}
                  value={rule.daily ?? ''}
                  onChange={(event) => {
                    const text = event.target.value.trim()
                    change(feature.value, {
                      ...rule,
                      daily: text === '' ? undefined : Math.max(0, Math.floor(Number(text))),
                    })
                  }}
                />
                <span className="muted">a day</span>
              </label>
              {open && (
                <span className="muted key-feature-used">
                  {used.data ? `Used today: ${today.toLocaleString()}` : 'Used today: …'}
                </span>
              )}
            </div>
          )
        })}
      </div>
    </details>
  )
}
