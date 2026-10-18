// What the strategy editor knows about each language. A language is added here (C++
// next), to `EditorLanguage`, and with its starter code to ./starters.
import { python } from '@codemirror/lang-python'
import { indentUnit } from '@codemirror/language'
import type { Extension } from '@codemirror/state'
import type { CodeLanguage } from '../api/types'

export type EditorLanguage = Extract<CodeLanguage, 'python'>

interface LanguageSupport {
  label: string
  extension: () => Extension
}

export const LANGUAGES: Record<EditorLanguage, LanguageSupport> = {
  python: {
    label: 'Python',
    // PEP 8 indentation; Tab inserts four spaces.
    extension: () => [python(), indentUnit.of('    ')],
  },
}
