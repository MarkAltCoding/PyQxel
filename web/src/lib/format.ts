// Number and date formatting. Returns and volatilities are decimals (0.25 = 25%).

const DASH = '—'

const missing = (value: number | null | undefined): value is null | undefined =>
  value === null || value === undefined || !Number.isFinite(value)

export function percent(value: number | null | undefined, digits = 1): string {
  if (missing(value)) return DASH
  const text = (value * 100).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
  return `${text}%`
}

export function signedPercent(value: number | null | undefined, digits = 1): string {
  if (missing(value)) return DASH
  const text = percent(value, digits)
  return value > 0 ? `+${text}` : text
}

export function number(value: number | null | undefined, digits = 2): string {
  if (missing(value)) return DASH
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function money(value: number | null | undefined, currency = 'USD', digits = 2): string {
  if (missing(value)) return DASH
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/** 1.2T, 340.5B, 12.0M: for market caps and volumes. */
export function compact(value: number | null | undefined, prefix = '$'): string {
  if (missing(value)) return DASH
  const units: [number, string][] = [
    [1e12, 'T'],
    [1e9, 'B'],
    [1e6, 'M'],
    [1e3, 'K'],
  ]
  for (const [size, unit] of units) {
    if (Math.abs(value) >= size) return `${prefix}${(value / size).toFixed(1)}${unit}`
  }
  return `${prefix}${value.toFixed(0)}`
}

export function shortDate(value: string | null | undefined): string {
  if (!value) return DASH
  const date = new Date(value.length === 10 ? `${value}T00:00:00` : value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return DASH
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

/** "+" / "-" class names for values whose sign is good or bad news. */
export function tone(value: number | null | undefined): 'up' | 'down' | undefined {
  if (missing(value) || value === 0) return undefined
  return value > 0 ? 'up' : 'down'
}
