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

describe('modelName with an effort', () => {
  it('names the effort after the model', () => {
    expect(modelName('claude-opus-5-5', 'high')).toBe('Claude Opus 5.5 at high effort')
    expect(modelName('claude-opus-5-5', 'xhigh')).toBe('Claude Opus 5.5 at extra-high effort')
  })

  it('puts the effort before the outside-the-app mark', () => {
    expect(modelName('Claude Opus 5.5 (outside the app)', 'max')).toBe(
      'Claude Opus 5.5 at maximum effort (outside the app)',
    )
  })

  it('leaves the effort out when there is none', () => {
    expect(modelName('claude (pasted)', null)).toBe('Claude outside the app (model not given)')
  })
})
