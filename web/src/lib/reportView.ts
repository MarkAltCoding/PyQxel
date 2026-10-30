// Whether Claude's reports are shown as panels or as one page of full text, remembered
// for this viewer.

import { useState } from 'react'
import { readStored, writeStored } from './storage'

export type ReportView = 'visual' | 'text'

const KEY = 'pyqxel.reportView'

export function useReportView(): [ReportView, (view: ReportView) => void] {
  const [view, setView] = useState<ReportView>(() =>
    readStored(KEY) === 'text' ? 'text' : 'visual',
  )
  return [
    view,
    (next) => {
      writeStored(KEY, next)
      setView(next)
    },
  ]
}
