import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { UpdateBanner } from './UpdateBanner'

const updater = vi.hoisted(() => ({ check: vi.fn() }))
const process = vi.hoisted(() => ({ relaunch: vi.fn() }))
vi.mock('@tauri-apps/plugin-updater', () => updater)
vi.mock('@tauri-apps/plugin-process', () => process)

/** A release newer than the running app, as the updater plugin describes one. */
function release(version = '0.2.0') {
  return {
    version,
    body: 'Faster backtests.\nA new chart.',
    downloadAndInstall: vi.fn(async (onEvent: (event: unknown) => void) => {
      onEvent({ event: 'Started', data: { contentLength: 100 } })
      onEvent({ event: 'Progress', data: { chunkLength: 100 } })
      onEvent({ event: 'Finished' })
    }),
  }
}

/** Renders the banner and lets its first check, five seconds in, run. */
async function renderAndWait() {
  render(<UpdateBanner />)
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000)
  })
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  updater.check.mockReset()
  process.relaunch.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
})

const inApp = () => {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
}

describe('UpdateBanner', () => {
  it('never looks for updates in a browser', async () => {
    await renderAndWait()
    expect(updater.check).not.toHaveBeenCalled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('stays quiet in the app when no newer release exists', async () => {
    inApp()
    updater.check.mockResolvedValue(null)
    await renderAndWait()
    expect(updater.check).toHaveBeenCalledOnce()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('offers a newer release, installs it, and restarts into it', async () => {
    inApp()
    const found = release()
    updater.check.mockResolvedValue(found)
    await renderAndWait()

    expect(await screen.findByText('PyQxel 0.2.0 is available')).toBeInTheDocument()
    expect(screen.getByText(/Faster backtests/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Update and restart' }))

    expect(found.downloadAndInstall).toHaveBeenCalledOnce()
    expect(process.relaunch).toHaveBeenCalledOnce()
  })

  it('remembers "Later" for that version only', async () => {
    inApp()
    updater.check.mockResolvedValue(release('0.2.0'))
    const { unmount } = render(<UpdateBanner />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Later' }))
    expect(screen.queryByText(/is available/)).not.toBeInTheDocument()
    unmount()

    await renderAndWait()
    expect(screen.queryByText(/0.2.0 is available/)).not.toBeInTheDocument()

    updater.check.mockResolvedValue(release('0.3.0'))
    await renderAndWait()
    expect(await screen.findByText('PyQxel 0.3.0 is available')).toBeInTheDocument()
  })

  it('offers the download page when installing fails', async () => {
    inApp()
    const found = release()
    found.downloadAndInstall.mockRejectedValue(new Error('disk full'))
    updater.check.mockResolvedValue(found)
    await renderAndWait()

    await userEvent.click(await screen.findByRole('button', { name: 'Update and restart' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('could not be installed')
    expect(screen.getByRole('link', { name: 'download it' })).toHaveAttribute(
      'href',
      'https://github.com/MarkAltCoding/PyQxel/releases/latest',
    )
    expect(process.relaunch).not.toHaveBeenCalled()
  })
})
