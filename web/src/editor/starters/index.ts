// The code a new custom strategy starts from, by language. Kept apart from the editor so
// pages can use it without loading CodeMirror.
import type { CodeLanguage } from '../../api/types'
import cpp from './strategy.cpp?raw'
import python from './strategy.py?raw'

const STARTERS: Record<CodeLanguage, string> = { python, cpp }

/** The code a new strategy in `language` starts from. */
export const starterCode = (language: CodeLanguage): string => STARTERS[language]
