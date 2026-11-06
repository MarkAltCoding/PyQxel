// Links that leave PyQxel. In the native app, a web view cannot open them itself (a tap
// on one with target="_blank" does nothing), so they go to the system browser, after
// asking; on the website they open in a new tab as usual.

/** Schemes that leave the app for another app: a web page, an email, or a call. */
const LEAVING = new Set(['http:', 'https:', 'mailto:', 'tel:'])

/**
 * The address a click on `target` would leave for, if it is on a link to another site
 * (or to email or a phone); `null` for a link within PyQxel or anything else.
 */
export function leavingHref(target: EventTarget | null, origin: string): string | null {
  if (!(target instanceof Element)) return null
  const anchor = target.closest('a[href]')
  if (!(anchor instanceof HTMLAnchorElement)) return null
  let url: URL
  try {
    url = new URL(anchor.getAttribute('href') ?? '', `${origin}/`)
  } catch {
    return null
  }
  if (!LEAVING.has(url.protocol)) return null
  if ((url.protocol === 'http:' || url.protocol === 'https:') && url.origin === origin) return null
  return url.href
}

/** How to name `href` to someone deciding whether to open it: its site, email or number. */
export function describeHref(href: string): string {
  const url = new URL(href)
  if (url.protocol === 'mailto:' || url.protocol === 'tel:') return decodeURIComponent(url.pathname)
  return url.hostname.replace(/^www\./, '')
}
