// About how many tokens a prompt taken outside the app holds, to warn before it is pasted
// into a chat that may not take it all. PyQxel's prompts, measured with Anthropic's token
// counting on November 20, 2026, run about 2.0 characters a token: numbers, tables, and
// JSON take more tokens than prose does.

const CHARACTERS_PER_TOKEN = 2.0

/** Above this many tokens, the prompt is flagged as long. */
export const LONG_PROMPT_TOKENS = 150_000

export const promptTokens = (prompt: string): number =>
  Math.round(prompt.length / CHARACTERS_PER_TOKEN / 1000) * 1000
