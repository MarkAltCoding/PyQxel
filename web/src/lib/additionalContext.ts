// What the person pastes for Claude to weigh beside the data: articles, research notes,
// transcripts, or the handoff an earlier list of Claude's Picks wrote. It is kept apart
// from the profile's notes, which say what to tailor to, and remembered for each page,
// so it survives a reload and can be cleared when done.

import { useState } from 'react'
import { readStored, writeStored } from './storage'

/** The longest additional context the API accepts. */
export const MAX_ADDITIONAL_CONTEXT = 60_000

export type ContextPage = 'analysis' | 'review' | 'picks'

const key = (page: ContextPage) => `pyqxel.additionalContext.${page}`

export function storedAdditionalContext(page: ContextPage): string {
  return (readStored(key(page)) ?? '').slice(0, MAX_ADDITIONAL_CONTEXT)
}

export function storeAdditionalContext(page: ContextPage, text: string): void {
  writeStored(key(page), text.slice(0, MAX_ADDITIONAL_CONTEXT))
}

/** The page's additional context and a setter that remembers it. */
export function useAdditionalContext(page: ContextPage): [string, (text: string) => void] {
  const [text, setText] = useState(() => storedAdditionalContext(page))
  return [
    text,
    (next) => {
      storeAdditionalContext(page, next)
      setText(next.slice(0, MAX_ADDITIONAL_CONTEXT))
    },
  ]
}

/** The context as a request carries it: trimmed, or null when there is none. */
export function contextForRequest(text: string): string | null {
  return text.trim() || null
}
