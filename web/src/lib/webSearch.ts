// Whether Claude may search the web for a report, remembered for this account: the same
// choice is offered for stock reports, portfolio reviews, and Claude's picks.

import { useState } from 'react'
import { readStored, writeStored } from './storage'

const KEY = 'pyqxel.webSearch'

export function useWebSearchChoice(): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(() => readStored(KEY) === 'on')
  return [
    on,
    (next) => {
      writeStored(KEY, next ? 'on' : null)
      setOn(next)
    },
  ]
}
