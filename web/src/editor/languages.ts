// What the strategy editor knows about each language. A language is added here, to
// `EditorLanguage`, and with its starter code to ./starters. Each language's grammar is
// its own chunk, loaded when the editor first shows that language, so Python users never
// download C++'s and the other way round.
import { indentUnit } from '@codemirror/language'
import type { Extension } from '@codemirror/state'
import type { CodeLanguage } from '../api/types'

export type EditorLanguage = Extract<CodeLanguage, 'python' | 'cpp'>

interface LanguageSupport {
  label: string
  load: () => Promise<Extension>
}

export const LANGUAGES: Record<EditorLanguage, LanguageSupport> = {
  python: {
    label: 'Python',
    // PEP 8 indentation; Tab inserts four spaces.
    load: async () => [(await import('@codemirror/lang-python')).python(), indentUnit.of('    ')],
  },
  cpp: {
    label: 'C++',
    // Four spaces, as the starter and pyqxel.h are written.
    load: async () => [(await import('@codemirror/lang-cpp')).cpp(), indentUnit.of('    ')],
  },
}
