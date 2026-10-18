// The strategy editor's look, drawn from the app's CSS variables so it follows the light
// and dark themes, and the theme toggle, without being rebuilt.
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { EditorView } from '@codemirror/view'
import { tags as t } from '@lezer/highlight'

const editorTheme = EditorView.theme({
  '&': {
    color: 'var(--text-primary)',
    backgroundColor: 'var(--surface-sunken)',
    border: '1px solid var(--border-strong)',
    borderRadius: 'var(--radius-small)',
    fontSize: '14px',
  },
  '&.cm-focused': { outline: '2px solid var(--focus)', outlineOffset: '1px' },
  '.cm-scroller': { fontFamily: 'var(--font-mono)', lineHeight: '1.55' },
  '.cm-content': { caretColor: 'var(--text-primary)', padding: '8px 0' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--text-primary)' },
  '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection':
    { backgroundColor: 'var(--accent-soft)' },
  '.cm-gutters': {
    backgroundColor: 'var(--surface-sunken)',
    color: 'var(--text-muted)',
    border: 'none',
    borderRight: '1px solid var(--border)',
    borderTopLeftRadius: 'var(--radius-small)',
    borderBottomLeftRadius: 'var(--radius-small)',
  },
  '.cm-activeLine': { backgroundColor: 'color-mix(in srgb, var(--accent-soft) 50%, transparent)' },
  '.cm-activeLineGutter': { backgroundColor: 'transparent', color: 'var(--text-secondary)' },
  '.cm-matchingBracket, &.cm-focused .cm-matchingBracket': {
    backgroundColor: 'var(--accent-soft)',
    outline: '1px solid var(--border-strong)',
  },
  '.cm-lintRange-error': {
    backgroundImage: 'none',
    textDecoration: 'underline wavy var(--critical-mark)',
    textDecorationSkipInk: 'none',
    textUnderlineOffset: '3px',
  },
  '.cm-tooltip': {
    backgroundColor: 'var(--surface-raised)',
    color: 'var(--text-primary)',
    border: '1px solid var(--border-strong)',
    borderRadius: 'var(--radius-small)',
  },
  '.cm-diagnostic': { fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap' },
  '.cm-diagnostic-error': { borderLeftColor: 'var(--critical-mark)' },
})

const highlightStyle = HighlightStyle.define([
  {
    tag: [t.keyword, t.controlKeyword, t.definitionKeyword, t.moduleKeyword, t.operatorKeyword],
    color: 'var(--code-keyword)',
  },
  { tag: [t.string, t.special(t.string), t.escape], color: 'var(--code-string)' },
  { tag: [t.number, t.bool, t.null], color: 'var(--code-number)' },
  {
    tag: [t.comment, t.lineComment, t.blockComment],
    color: 'var(--code-comment)',
    fontStyle: 'italic',
  },
  {
    tag: [
      t.function(t.variableName),
      t.function(t.definition(t.variableName)),
      t.function(t.propertyName),
    ],
    color: 'var(--code-function)',
  },
  { tag: [t.className, t.definition(t.className), t.typeName], color: 'var(--code-class)' },
  { tag: t.invalid, color: 'var(--critical)' },
])

export const appearance = [editorTheme, syntaxHighlighting(highlightStyle)]
