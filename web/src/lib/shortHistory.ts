// A backtest asks before running on a shorter price history than its window: the server
// answers such a run with 409, and the browser checks the bars it fetched for custom code.
import { ApiError } from '../api/client'

/** Bars for custom code were shorter than the window asks for, for the reason given. */
export class ShortHistoryError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ShortHistoryError'
  }
}

/** The plain warning when `error` is a refusal to run on a shorter history, else null. */
export function shortHistoryMessage(error: unknown): string | null {
  if (error instanceof ShortHistoryError) return error.message
  if (error instanceof ApiError && error.status === 409) return error.message
  return null
}
