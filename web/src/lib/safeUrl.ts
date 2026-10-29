// Links to pages outside the app come from data providers, or from data pasted back with
// an answer; only web addresses are linked, never javascript:, data: or other schemes.

/** `url` if it is an http or https address, else undefined, so no link is made. */
export function safeUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : undefined
  } catch {
    return undefined
  }
}
