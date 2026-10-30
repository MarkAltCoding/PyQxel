// The ticker symbols a strategy's code names, so a strategy across tickers needs no
// symbols chosen apart from its code: each one its strings name is fetched for it.

import type { CodeLanguage } from '../api/types'

/** A ticker as Yahoo writes them: SPY, BRK-B, RDS.A, ^GSPC, GC=F, EURUSD=X. */
const TICKER = /^\^?[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,4})?(?:=[A-Z])?$/

/** Upper-case strings code uses that are not tickers: pandas frequencies such as "ME"
 * (and "W-FRI", by prefix) and missing-value markers. Like a single letter, one of these
 * is still fetched when the code lists it in SYMBOLS. */
const NOT_TICKERS = new Set(
  (
    'AS BAS BH BM BME BMS BQ BQE BQS BY BYE BYS CBM CBME CBMS ME MS QE QS SM SME SMS YE YS ' +
    'NA NAN NONE TRUE FALSE UTC GMT OK'
  ).split(' '),
)

/** A list the code declares its symbols in: SYMBOLS = [...], TICKERS = {...}. */
const DECLARED = /\b(?:SYMBOLS|TICKERS|UNIVERSE)\b[^=\n]*=\s*[[({]([^\])}]*)[\])}]/g

/** Each string literal in the code, skipping comments, which name tickers in prose. */
function strings(code: string, language: CodeLanguage): string[] {
  const pattern =
    language === 'python'
      ? /("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|#[^\n]*/g
      : /("(?:[^"\\\n]|\\.)*")|\/\/[^\n]*|\/\*[\s\S]*?\*\//g
  const found: string[] = []
  for (const match of code.matchAll(pattern)) {
    if (match[1]) found.push(match[1].slice(1, -1))
  }
  return found
}

/** The tickers ``code`` names, those it declares in SYMBOLS first, each once, in order. */
export function tickersIn(code: string, language: CodeLanguage): string[] {
  const declared = [...code.matchAll(DECLARED)].flatMap((match) =>
    strings(match[1] ?? '', language).filter((text) => TICKER.test(text)),
  )
  const named = strings(code, language).filter(
    (text) =>
      TICKER.test(text) && text.length > 1 && !NOT_TICKERS.has(text) && !text.startsWith('W-'),
  )
  return [...new Set([...declared, ...named])]
}
