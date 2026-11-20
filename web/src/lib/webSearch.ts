// Whether Claude may use information from outside the app (search the web) for a report,
// remembered for this account: the same choice is offered for stock reports, portfolio
// reviews, and Claude's picks. It is on until the person turns it off, and it also tells
// Claude outside the app whether it may search the web itself.

import { useState } from 'react'
import { readStored, writeStored } from './storage'

const KEY = 'pyqxel.webSearch'

export function useWebSearchChoice(): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(() => readStored(KEY) !== 'off')
  return [
    on,
    (next) => {
      writeStored(KEY, next ? null : 'off')
      setOn(next)
    },
  ]
}
