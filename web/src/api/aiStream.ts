// Reads one of Claude's Server-Sent Event streams: reasoning summaries and output
// fragments while it writes, then the result or an error.
import { ApiError } from './client'
import { readEvents } from './sse'

export interface AiProgress {
  /** The model's summarized reasoning so far. */
  thinking: string
  /** Characters of the answer written so far. */
  characters: number
  /** What Claude has searched the web for so far, when it was asked to. */
  searches: string[]
}

/** Progress before anything has arrived. */
export const NO_PROGRESS: AiProgress = { thinking: '', characters: 0, searches: [] }

/**
 * Follows `response` to its `result` event and returns that event's data, reporting
 * progress as it goes. Throws an ApiError for an `error` event or a stream that ends
 * without a result.
 */
export async function readAiStream<T>(
  response: Response,
  onProgress: (progress: AiProgress) => void,
): Promise<T> {
  const progress: AiProgress = { ...NO_PROGRESS, searches: [] }
  for await (const event of readEvents(response)) {
    if (event.event === 'thinking' || event.event === 'report') {
      const { text } = JSON.parse(event.data) as { text: string }
      if (event.event === 'thinking') progress.thinking += text
      else progress.characters += text.length
      onProgress({ ...progress })
    } else if (event.event === 'search') {
      progress.searches = [
        ...progress.searches,
        (JSON.parse(event.data) as { query: string }).query,
      ]
      onProgress({ ...progress })
    } else if (event.event === 'result') {
      return JSON.parse(event.data) as T
    } else if (event.event === 'error') {
      const failure = JSON.parse(event.data) as {
        status: number
        detail: string
        retry_after: number | null
      }
      throw new ApiError(failure.status, failure.detail, failure.retry_after)
    }
  }
  throw new ApiError(502, 'The answer stopped before it was finished. Please try again.')
}
