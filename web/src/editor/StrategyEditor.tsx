// A code editor for custom strategies, on CodeMirror 6: unlike Monaco, it works with
// touch screens and on-screen keyboards, so it suits the iPhone app as well as the web.
import { useEffect, useRef } from 'react'
import { autocompletion, closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { bracketMatching, indentOnInput } from '@codemirror/language'
import { lintGutter, lintKeymap, setDiagnostics, type Diagnostic } from '@codemirror/lint'
import { Compartment, EditorState } from '@codemirror/state'
import {
  drawSelection,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from '@codemirror/view'
import { LANGUAGES, type EditorLanguage } from './languages'
import { appearance } from './theme'

/** An error from the last run, marked on its line when it has one. */
export interface EditorError {
  line: number | null
  message: string
}

export interface StrategyEditorProps {
  language: EditorLanguage
  value: string
  onChange: (value: string) => void
  error?: EditorError | null
  /** Names the editor for screen readers. */
  label?: string
  readOnly?: boolean
}

/** The diagnostic marking `error`'s whole line, or none when it has no line in `state`. */
function diagnostics(state: EditorState, error: EditorError | null): Diagnostic[] {
  if (!error?.line || error.line < 1 || error.line > state.doc.lines) return []
  const line = state.doc.line(error.line)
  // Leading indentation is left unmarked, as editors usually do.
  const indent = line.text.length - line.text.trimStart().length
  const from = Math.min(line.from + indent, line.to)
  return [{ from, to: line.to, severity: 'error', message: error.message }]
}

export function StrategyEditor({
  language,
  value,
  onChange,
  error = null,
  label = 'Strategy code',
  readOnly = false,
}: StrategyEditorProps) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const changed = useRef(onChange)
  const compartments = useRef({ language: new Compartment(), readOnly: new Compartment() })
  /** The props the view starts from; the effects after its creation apply later ones. */
  const initial = useRef({ language, value, label, readOnly })

  useEffect(() => {
    changed.current = onChange
  }, [onChange])

  // The view is created once; later prop changes reconfigure it rather than rebuild it, so
  // the cursor, selection and undo history survive them.
  useEffect(() => {
    const { language: languageSlot, readOnly: readOnlySlot } = compartments.current
    const start = initial.current
    const created = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: start.value,
        extensions: [
          lineNumbers(),
          highlightActiveLineGutter(),
          lintGutter(),
          history(),
          drawSelection(),
          indentOnInput(),
          bracketMatching(),
          closeBrackets(),
          autocompletion({ activateOnTyping: false }),
          highlightActiveLine(),
          // Long lines wrap rather than scroll, so nothing, an error mark included, is out of
          // sight on a phone.
          EditorView.lineWrapping,
          EditorState.tabSize.of(4),
          // Tab indents; Escape then Tab moves focus out, so keyboard users are not trapped.
          keymap.of([
            ...closeBracketsKeymap,
            ...defaultKeymap,
            ...historyKeymap,
            ...lintKeymap,
            indentWithTab,
          ]),
          languageSlot.of(LANGUAGES[start.language].extension()),
          readOnlySlot.of(EditorState.readOnly.of(start.readOnly)),
          EditorView.contentAttributes.of({
            'aria-label': start.label,
            autocapitalize: 'off',
            autocorrect: 'off',
            spellcheck: 'false',
          }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) changed.current(update.state.doc.toString())
          }),
          appearance,
        ],
      }),
    })
    view.current = created
    return () => {
      created.destroy()
      view.current = null
    }
  }, [])

  // Code set from outside, as when a saved strategy is opened, replaces the document.
  useEffect(() => {
    const current = view.current
    if (!current || current.state.doc.toString() === value) return
    current.dispatch({ changes: { from: 0, to: current.state.doc.length, insert: value } })
  }, [value])

  useEffect(() => {
    view.current?.dispatch({
      effects: compartments.current.language.reconfigure(LANGUAGES[language].extension()),
    })
  }, [language])

  useEffect(() => {
    view.current?.dispatch({
      effects: compartments.current.readOnly.reconfigure(EditorState.readOnly.of(readOnly)),
    })
  }, [readOnly])

  // A new error is marked and scrolled into sight, since on a phone its line may be far
  // below what the editor shows.
  useEffect(() => {
    const current = view.current
    if (!current) return
    const marked = diagnostics(current.state, error)
    current.dispatch(
      setDiagnostics(current.state, marked),
      ...marked.map((mark) => ({ effects: EditorView.scrollIntoView(mark.from, { y: 'center' }) })),
    )
  }, [error])

  return <div ref={host} className="strategy-editor" data-language={language} />
}
