import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { EditorView } from '@codemirror/view'
import { forEachDiagnostic } from '@codemirror/lint'
import { StrategyEditor, type EditorError } from './StrategyEditor'
import { starterCode } from './starters'

const CODE = 'def strategy(bars):\n    x = 1\n    return bars["Nope"]\n'

function mount(props: { value?: string; error?: EditorError | null; readOnly?: boolean } = {}) {
  const onChange = vi.fn()
  const element = (overrides: typeof props) => (
    <StrategyEditor
      language="python"
      value={overrides.value ?? props.value ?? CODE}
      onChange={onChange}
      error={overrides.error === undefined ? props.error : overrides.error}
      readOnly={overrides.readOnly ?? props.readOnly}
    />
  )
  const rendered = render(element({}))
  const editor = rendered.container.querySelector<HTMLElement>('.cm-editor')!
  const view = EditorView.findFromDOM(editor)!
  return {
    ...rendered,
    view,
    onChange,
    update: (overrides: typeof props) => rendered.rerender(element(overrides)),
  }
}

const marked = (view: EditorView) => {
  const found: { line: number; text: string; message: string }[] = []
  forEachDiagnostic(view.state, (diagnostic, from, to) =>
    found.push({
      line: view.state.doc.lineAt(from).number,
      text: view.state.sliceDoc(from, to),
      message: diagnostic.message,
    }),
  )
  return found
}

describe('StrategyEditor', () => {
  it('shows the code with Python highlighting and an accessible name', () => {
    const { view, container } = mount()

    expect(view.state.doc.toString()).toBe(CODE)
    expect(container.querySelector('[data-language="python"]')).not.toBeNull()
    expect(view.contentDOM.getAttribute('aria-label')).toBe('Strategy code')
    expect(view.contentDOM.getAttribute('autocapitalize')).toBe('off')
  })

  it('reports edits', () => {
    const { view, onChange } = mount()

    view.dispatch({ changes: { from: 0, insert: '# mine\n' } })

    expect(onChange).toHaveBeenLastCalledWith(`# mine\n${CODE}`)
  })

  it('replaces the document when the code is set from outside', () => {
    const { view, update } = mount()

    update({ value: 'def strategy(bars):\n    return 1\n' })

    expect(view.state.doc.toString()).toBe('def strategy(bars):\n    return 1\n')
  })

  it('marks the line an error points at, without its indentation', () => {
    const { view, update } = mount()

    update({ error: { line: 3, message: "KeyError: 'Nope'" } })

    expect(marked(view)).toEqual([
      { line: 3, text: 'return bars["Nope"]', message: "KeyError: 'Nope'" },
    ])
  })

  it('clears the mark with the error, and marks nothing for errors without a line', () => {
    const { view, update } = mount({ error: { line: 2, message: 'NameError' } })
    expect(marked(view)).toHaveLength(1)

    update({ error: null })
    expect(marked(view)).toEqual([])

    update({ error: { line: null, message: 'The position is NaN on 3 bars.' } })
    expect(marked(view)).toEqual([])

    update({ error: { line: 99, message: 'Past the end' } })
    expect(marked(view)).toEqual([])
  })

  it('can be made read-only', () => {
    const { view, update } = mount()
    expect(view.state.readOnly).toBe(false)

    update({ readOnly: true })

    expect(view.state.readOnly).toBe(true)
  })

  it('starts Python strategies from a template showing the contract and the warmup', () => {
    const starter = starterCode('python')

    expect(starter).toContain('def strategy(bars: pd.DataFrame) -> pd.Series:')
    expect(starter).toContain('.fillna(0.0)')
    expect(starter).toContain('holds each position over the next bar')
  })
})
