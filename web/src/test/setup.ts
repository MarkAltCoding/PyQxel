import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// jsdom has no layout, so text ranges have no boxes; CodeMirror measures them when it
// scrolls a line into view.
if (typeof Range !== 'undefined' && !Range.prototype.getClientRects) {
  Range.prototype.getClientRects = () => [] as unknown as DOMRectList
  Range.prototype.getBoundingClientRect = () => new DOMRect()
}

afterEach(() => {
  cleanup()
  // Tests that run under Node instead of jsdom, such as the C++ compiler's, have no window.
  if (typeof window !== 'undefined') window.localStorage.clear()
})
