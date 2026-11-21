// How long the person will wait for more data where a provider's per-minute limit holds it
// back, remembered for this account: the same choice is offered for stock reports,
// portfolio reviews, and Claude's Picks, and goes into the prompt taken outside the app too.
// A free Finnhub plan answers 60 requests a minute, so the news, analysts' ratings, and
// calendar of many stocks take several minutes; the report's notice says what more waiting
// would have added.

import { useState } from 'react'
import { readStored, writeStored } from './storage'

const KEY = 'pyqxel.waitForData'

export const WAIT_CHOICES: { value: number; label: string }[] = [
  { value: 0, label: 'No waiting' },
  { value: 60, label: 'Up to 1 minute' },
  { value: 120, label: 'Up to 2 minutes' },
  { value: 300, label: 'Up to 5 minutes' },
  { value: 600, label: 'Up to 10 minutes' },
  { value: 900, label: 'Up to 15 minutes' },
]

/** Five minutes unless the person chose otherwise: enough for a full picks list or a
 * review of 40 holdings on a free Finnhub plan. */
export const DEFAULT_WAIT_SECONDS = 300

export function useWaitChoice(): [number, (seconds: number) => void] {
  const [seconds, setSeconds] = useState(() => {
    const stored = Number(readStored(KEY))
    return WAIT_CHOICES.some((choice) => choice.value === stored) && readStored(KEY) !== null
      ? stored
      : DEFAULT_WAIT_SECONDS
  })
  return [
    seconds,
    (next) => {
      writeStored(KEY, String(next))
      setSeconds(next)
    },
  ]
}
