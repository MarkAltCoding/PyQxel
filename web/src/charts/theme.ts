import { useEffect, useState } from 'react'

/** Chart colors and ink, read from the CSS tokens so charts follow the page's theme. */
export interface ChartTheme {
  surface: string
  textPrimary: string
  textSecondary: string
  textMuted: string
  grid: string
  axis: string
  series1: string
  series2: string
  seq100: string
  seq200: string
  seq300: string
  seq450: string
  seq600: string
  divNeg: string
  divMid: string
  divPos: string
  font: string
}

const TOKENS: Record<keyof ChartTheme, string> = {
  surface: '--surface',
  textPrimary: '--text-primary',
  textSecondary: '--text-secondary',
  textMuted: '--text-muted',
  grid: '--grid',
  axis: '--axis',
  series1: '--series-1',
  series2: '--series-2',
  seq100: '--seq-100',
  seq200: '--seq-200',
  seq300: '--seq-300',
  seq450: '--seq-450',
  seq600: '--seq-600',
  divNeg: '--div-neg',
  divMid: '--div-mid',
  divPos: '--div-pos',
  font: '--font',
}

export function readTheme(): ChartTheme {
  const style = getComputedStyle(document.documentElement)
  const theme = {} as ChartTheme
  for (const [name, token] of Object.entries(TOKENS) as [keyof ChartTheme, string][]) {
    theme[name] = style.getPropertyValue(token).trim()
  }
  return theme
}

/** The current chart theme, updated when the OS scheme or the page's theme toggle changes. */
export function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState(readTheme)
  useEffect(() => {
    const update = () => setTheme(readTheme())
    const media = window.matchMedia?.('(prefers-color-scheme: dark)')
    media?.addEventListener('change', update)
    const observer = new MutationObserver(update)
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    })
    return () => {
      media?.removeEventListener('change', update)
      observer.disconnect()
    }
  }, [])
  return theme
}

/** Escapes text for ECharts' HTML tooltips; symbols and names come from the API. */
export function escapeHtml(text: string): string {
  return text.replace(
    /[&<>"']/g,
    (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!,
  )
}

/** Shared tooltip chrome: surface, hairline border, primary ink. */
export function tooltipStyle(theme: ChartTheme) {
  return {
    backgroundColor: theme.surface,
    borderColor: theme.axis,
    borderWidth: 1,
    padding: [8, 10],
    textStyle: { color: theme.textPrimary, fontFamily: theme.font, fontSize: 12 },
    extraCssText: 'box-shadow: 0 4px 16px rgba(0,0,0,0.12); border-radius: 6px;',
  }
}

/** A tooltip row: a short line key in the series color, the value strong, the name after. */
export function tooltipRow(color: string, value: string, label: string, dashed = false): string {
  const key = dashed ? `border-top:2px dashed ${color};height:0` : `background:${color};height:2px`
  return (
    `<div style="display:flex;align-items:center;gap:8px;margin-top:4px">` +
    `<span style="display:inline-block;width:12px;${key}"></span>` +
    `<strong>${escapeHtml(value)}</strong>` +
    `<span style="opacity:.7">${escapeHtml(label)}</span></div>`
  )
}

export function axisStyle(theme: ChartTheme) {
  return {
    axisLine: { lineStyle: { color: theme.axis } },
    axisTick: { show: false },
    axisLabel: { color: theme.textMuted, fontFamily: theme.font, fontSize: 11 },
    splitLine: { lineStyle: { color: theme.grid, width: 1 } },
  }
}
