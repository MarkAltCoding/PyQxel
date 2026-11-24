// The effort used for an analysis written by Claude outside the app, as the person notes it
// beside the pasted answer, so a saved report and its performance say exactly what wrote
// it. Claude names its own model in the answer, but it cannot tell its effort: that is a
// setting of the request, not something it reads. The last choice is remembered, since
// most people keep to one setup.

import { readStored, writeStored } from './storage'

export type OutsideEffort = 'low' | 'medium' | 'high' | 'xhigh' | 'max'

const KEY = 'pyqxel.outsideEffort'

export const OUTSIDE_EFFORTS: { value: OutsideEffort | ''; label: string }[] = [
  { value: '', label: 'Not noted' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'xhigh', label: 'Extra-high' },
  { value: 'max', label: 'Maximum' },
]

export function storedOutsideEffort(): OutsideEffort | null {
  const stored = readStored(KEY)
  return OUTSIDE_EFFORTS.some((effort) => effort.value === stored && stored !== '')
    ? (stored as OutsideEffort)
    : null
}

export function storeOutsideEffort(effort: OutsideEffort | null): void {
  writeStored(KEY, effort ?? '')
}
