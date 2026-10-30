// Scrolling to the section a link names after "#". Browsers do it only on a full page
// load; in the app, pages change without one, and the section may only render once the
// page's data has arrived.

import { useEffect } from 'react'
import { useLocation } from 'react-router'

/** How long to wait for a linked section to appear before giving up. */
const WAIT_MS = 5_000

/** The element a URL fragment names, by id; null when there is none yet. */
function target(hash: string): HTMLElement | null {
  try {
    const id = decodeURIComponent(hash.slice(1))
    return id ? document.getElementById(id) : null
  } catch {
    // A malformed fragment names no section.
    return null
  }
}

/** Scrolls to the section the URL's fragment names, on arrival and whenever it changes,
 * waiting a few seconds for it to render. */
export function useScrollToHash(): void {
  const { hash, key } = useLocation()
  useEffect(() => {
    if (hash.length < 2) return
    const scroll = (element: HTMLElement) => element.scrollIntoView?.({ block: 'start' })
    const found = target(hash)
    if (found) {
      scroll(found)
      return
    }
    const observer = new MutationObserver(() => {
      const element = target(hash)
      if (element) {
        scroll(element)
        stop()
      }
    })
    const timer = window.setTimeout(() => stop(), WAIT_MS)
    function stop() {
      observer.disconnect()
      window.clearTimeout(timer)
    }
    observer.observe(document.body, { childList: true, subtree: true })
    return stop
  }, [hash, key])
}
