// The code a new custom strategy starts from, by language, for one symbol or several.
// Kept apart from the editor so pages can use it without loading CodeMirror.
import type { CodeLanguage } from '../../api/types'
import cpp from './strategy.cpp?raw'
import python from './strategy.py?raw'
import universeCpp from './universe.cpp?raw'
import universePython from './universe.py?raw'

const STARTERS: Record<CodeLanguage, string> = { python, cpp }
const UNIVERSE_STARTERS: Record<CodeLanguage, string> = {
  python: universePython,
  cpp: universeCpp,
}

/** The code a new strategy in `language` starts from: across several symbols when
 * `universe` is set, on one otherwise. */
export const starterCode = (language: CodeLanguage, universe = false): string =>
  (universe ? UNIVERSE_STARTERS : STARTERS)[language]
