// Tones AI reports grade things in, from reassuring to alarming.

export type Tone = 'good' | 'neutral' | 'warning' | 'critical'

/** Risk and severity grades, least severe first. */
export const SEVERITY_ORDER = ['low', 'moderate', 'elevated', 'high'] as const

export const SEVERITY_TONE: Record<(typeof SEVERITY_ORDER)[number], Tone> = {
  low: 'good',
  moderate: 'neutral',
  elevated: 'warning',
  high: 'critical',
}

/** The badge class for a tone; neutral badges take none. */
export const badgeTone = (tone: Tone): string => (tone === 'neutral' ? '' : tone)
