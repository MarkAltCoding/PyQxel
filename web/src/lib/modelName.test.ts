import { describe, expect, it } from 'vitest'
import { modelName } from './modelName'

describe('modelName', () => {
  it('reads API model IDs and keeps what Claude named itself outside the app', () => {
    expect(modelName('claude-opus-5-5')).toBe('Claude Opus 5.5')
    expect(modelName('claude-fable-5')).toBe('Claude Fable 5')
    expect(modelName('Claude Opus 5.5 (outside the app)')).toBe('Claude Opus 5.5 (outside the app)')
    expect(modelName('claude (pasted)')).toBe('Claude outside the app (model not given)')
  })
})
