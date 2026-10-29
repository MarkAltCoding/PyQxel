// The data each exported Claude prompt was built from, kept in this browser so Claude's
// answer can be saved with it, even after the page is reloaded.
import { readStored, writeStored } from './storage'

const PREFIX = 'pyqxel.exportedPrompt.'

export function keepExport<T>(name: string, value: T): void {
  writeStored(PREFIX + name, JSON.stringify(value))
}

/** The kept export, or null when there is none or it cannot be read. */
export function keptExport<T>(name: string): T | null {
  try {
    const text = readStored(PREFIX + name)
    return text ? (JSON.parse(text) as T) : null
  } catch {
    return null
  }
}
