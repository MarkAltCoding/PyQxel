import { readStored, writeStored } from './storage'

export type Theme = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'pyqxel.theme'

export function storedTheme(): Theme {
  return (readStored(STORAGE_KEY) as Theme | null) ?? 'system'
}

/** Sets the page's theme; "system" follows the OS. */
export function applyTheme(theme: Theme): void {
  if (theme === 'system') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = theme
}

export function saveTheme(theme: Theme): void {
  applyTheme(theme)
  writeStored(STORAGE_KEY, theme === 'system' ? null : theme)
}
