// The code a new custom strategy starts from, by language, for one symbol or several.
// Kept apart from the editor so pages can use it without loading CodeMirror.
import type { CodeLanguage } from '../../api/types'
import acrossCpp from './across.cpp?raw'
import acrossPython from './across.py?raw'
import cpp from './strategy.cpp?raw'
import python from './strategy.py?raw'
import universeCpp from './universe.cpp?raw'
import universePython from './universe.py?raw'

const STARTERS: Record<CodeLanguage, string> = { python, cpp }
const UNIVERSE_STARTERS: Record<CodeLanguage, string> = {
  python: universePython,
  cpp: universeCpp,
}

const ACROSS_STARTERS: Record<CodeLanguage, string> = { python: acrossPython, cpp: acrossCpp }

/** What a custom strategy trades: one symbol, the symbols chosen beside it, or the
 * tickers its own code names. */
export type CodeKind = 'one' | 'several' | 'across'

/** The code a new strategy of this kind in `language` starts from. */
export const starterCode = (language: CodeLanguage, kind: CodeKind = 'one'): string =>
  ({ one: STARTERS, several: UNIVERSE_STARTERS, across: ACROSS_STARTERS })[kind][language]
