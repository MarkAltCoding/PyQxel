import { useState } from 'react'
import { saveTheme, storedTheme, type Theme } from '../lib/theme'

const NEXT: Record<Theme, Theme> = { system: 'light', light: 'dark', dark: 'system' }
const LABEL: Record<Theme, string> = { system: 'Auto', light: 'Light', dark: 'Dark' }

/** Cycles the color theme: following the OS, light, or dark. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(storedTheme)
  return (
    <button
      type="button"
      className="button ghost small"
      title="Color theme"
      onClick={() => {
        const next = NEXT[theme]
        saveTheme(next)
        setTheme(next)
      }}
    >
      Theme: {LABEL[theme]}
    </button>
  )
}
