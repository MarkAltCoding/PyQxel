// Reads a Server-Sent Events stream from a fetch response. EventSource cannot POST or
// send headers, so the analysis stream is read this way instead.

export interface ServerEvent {
  event: string
  data: string
}

/** Splits SSE text into events; returns the events and any incomplete trailing text. */
export function parseEvents(buffer: string): { events: ServerEvent[]; rest: string } {
  const events: ServerEvent[] = []
  const blocks = buffer.replace(/\r\n?/g, '\n').split('\n\n')
  const rest = blocks.pop() ?? ''
  for (const block of blocks) {
    let event = 'message'
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (!line || line.startsWith(':')) continue
      const colon = line.indexOf(':')
      const field = colon === -1 ? line : line.slice(0, colon)
      let value = colon === -1 ? '' : line.slice(colon + 1)
      if (value.startsWith(' ')) value = value.slice(1)
      if (field === 'event') event = value
      else if (field === 'data') data.push(value)
    }
    if (data.length > 0) events.push({ event, data: data.join('\n') })
  }
  return { events, rest }
}

export async function* readEvents(response: Response): AsyncGenerator<ServerEvent> {
  if (!response.body) return
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    const { events, rest } = parseEvents(buffer)
    buffer = rest
    yield* events
  }
  const { events } = parseEvents(buffer + '\n\n')
  yield* events
}
