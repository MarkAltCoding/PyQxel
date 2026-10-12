import { describe, expect, it } from 'vitest'
import { parseEvents, readEvents } from './sse'

describe('parseEvents', () => {
  it('splits complete events and keeps the incomplete tail', () => {
    const { events, rest } = parseEvents(
      'event: context\ndata: {"a":1}\n\nevent: report\ndata: {"text":"x"',
    )
    expect(events).toEqual([{ event: 'context', data: '{"a":1}' }])
    expect(rest).toBe('event: report\ndata: {"text":"x"')
  })

  it('ignores keep-alive comments and joins multi-line data', () => {
    const { events } = parseEvents(': ping\n\ndata: one\ndata: two\n\n')
    expect(events).toEqual([{ event: 'message', data: 'one\ntwo' }])
  })

  it('accepts CRLF line endings', () => {
    const { events } = parseEvents('event: result\r\ndata: {}\r\n\r\n')
    expect(events).toEqual([{ event: 'result', data: '{}' }])
  })
})

describe('readEvents', () => {
  it('reassembles events split across network chunks', async () => {
    const chunks = ['event: thinking\nda', 'ta: {"text":"Hm', '"}\n\nevent: result\ndata: {}\n\n']
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk))
        controller.close()
      },
    })
    const seen = []
    for await (const event of readEvents(new Response(stream))) seen.push(event)
    expect(seen).toEqual([
      { event: 'thinking', data: '{"text":"Hm"}' },
      { event: 'result', data: '{}' },
    ])
  })
})
