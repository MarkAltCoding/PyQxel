import { describe, expect, it } from 'vitest'
import { tickersIn } from './tickers'

describe('tickersIn', () => {
  it('finds the tickers Python strings name, declared ones first, each once', () => {
    const code = `
# Compare with "IGNORED" in a comment.
SYMBOLS = ["SPY", 'TLT', "T"]
vix = bars["Close"]["^VIX"]
gold = bars["Close"].get("GC=F")
brk = close["BRK-B"] / close["SPY"]
monthly = close.resample("ME").last()
weekly = close.resample("W-FRI").last()
label = "Close"
`
    expect(tickersIn(code, 'python')).toEqual(['SPY', 'TLT', 'T', '^VIX', 'GC=F', 'BRK-B'])
  })

  it('finds C++ string literals, skipping comments', () => {
    const code = `
// Uses "IGNORED" only in a comment.
/* and "ALSO" here */
const char* const TICKERS[] = {"QQQ", "IWM"};
std::size_t spy = universe.find("SPY");
`
    expect(tickersIn(code, 'cpp')).toEqual(['QQQ', 'IWM', 'SPY'])
  })

  it('leaves out single letters unless the code declares them', () => {
    expect(tickersIn('x = close["F"]', 'python')).toEqual([])
    expect(tickersIn('SYMBOLS = ["F"]\nx = close["F"]', 'python')).toEqual(['F'])
  })
})
