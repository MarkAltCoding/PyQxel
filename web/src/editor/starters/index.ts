// The code a new custom strategy starts from, by language. Kept apart from the editor so
// pages can use it without loading CodeMirror.
import type { EditorLanguage } from '../languages'
import python from './strategy.py?raw'

const STARTERS: Record<EditorLanguage, string> = { python }

/** The code a new strategy in `language` starts from. */
export const starterCode = (language: EditorLanguage): string => STARTERS[language]
