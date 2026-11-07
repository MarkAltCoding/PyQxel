// Updates for the desktop app. Each GitHub release carries a signed copy of the app and a
// latest.json naming its version (see .github/workflows/release.yml); the updater plugin
// reads that file, checks the signature against the public key in tauri.conf.json, and
// installs the new version over the running one. Phones update through their app stores,
// so there this finds nothing, as it does in a browser, where every visit is current.
//
// Nothing is offered until a release newer than the running app is published, so while
// no release is made, this stays quiet. A build for the Mac App Store, which updates apps
// itself, turns it off with VITE_APP_UPDATES=off.

import { isNativeApp } from './native'
import { readStored, writeStored } from './storage'

/** How often a running app looks for a new release. */
export const CHECK_EVERY_MS = 6 * 60 * 60 * 1000

const DISMISSED_KEY = 'pyqxel.updateDismissed'

/** A newer release, ready to install. */
export interface AvailableUpdate {
  version: string
  /** The release notes' first lines, if any. */
  notes: string | null
  /** Downloads and installs it, reporting the share done (0 to 1, or null when unknown),
   * then restarts the app into it. */
  install: (onProgress: (share: number | null) => void) => Promise<void>
}

/** Whether this build looks for updates at all. */
export function updatesEnabled(): boolean {
  return isNativeApp() && import.meta.env.VITE_APP_UPDATES !== 'off'
}

/** The newer release, or null when there is none, this is not a desktop app, or the
 * check failed (offline, or no release published yet). */
export async function findUpdate(): Promise<AvailableUpdate | null> {
  if (!updatesEnabled()) return null
  try {
    const { check } = await import('@tauri-apps/plugin-updater')
    const update = await check()
    if (!update) return null
    return {
      version: update.version,
      notes: summarizeNotes(update.body),
      install: async (onProgress) => {
        let total: number | null = null
        let done = 0
        await update.downloadAndInstall((event) => {
          if (event.event === 'Started') total = event.data.contentLength ?? null
          if (event.event === 'Progress') {
            done += event.data.chunkLength
            onProgress(total ? Math.min(1, done / total) : null)
          }
        })
        const { relaunch } = await import('@tauri-apps/plugin-process')
        await relaunch()
      },
    }
  } catch {
    // On a phone the updater is not built in; elsewhere the release may be unreachable.
    return null
  }
}

/** The first few lines of a release's notes, as plain text: Markdown headings, link-only
 * lines (such as GitHub's "Full Changelog"), and list markers are left out. */
export function summarizeNotes(body: string | undefined, lines = 3): string | null {
  const kept = (body ?? '')
    .split('\n')
    .map((line) => line.trim().replace(/^[-*]\s+/, ''))
    .filter((line) => line && !line.startsWith('#') && !/^\**full changelog/i.test(line))
    .filter((line) => !/^\S*https?:\/\/\S+$/.test(line))
  return kept.length ? kept.slice(0, lines).join('\n') : null
}

/** Whether the person chose "Later" for `version`; a newer one is offered again. */
export function dismissedUpdate(version: string): boolean {
  return readStored(DISMISSED_KEY) === version
}

export function dismissUpdate(version: string): void {
  writeStored(DISMISSED_KEY, version)
}
