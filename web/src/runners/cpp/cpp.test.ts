// @vitest-environment node
// Compiles and runs real C++ with the YoWASP toolchain and the WASI shim, through the
// same functions the worker calls.
import { commands } from '@yowasp/clang'
import { describe, expect, it } from 'vitest'
import { starterCode } from '../../editor/starters'
import type { BarColumns, UniverseColumns } from '../types'
import type { PositionsOutcome, WeightsOutcome } from '../workerRunner'
import { checkPositions, checkWeights, runStrategy, runUniverseStrategy } from './execute'
import { compileError, compileStrategy } from './compile'

const clang = commands['clang++']
const TIMEOUT = 120_000

/** `count` daily bars in New York time: a slow rise with a wave, low missing on bar 3. */
function bars(count: number): BarColumns {
  const day = (i: number) =>
    new Date(Date.UTC(2025, 0, 2, 5) + i * 86_400_000).toISOString().replace('.000Z', '-05:00')
  const close = Array.from({ length: count }, (_, i) => 100 + i * 0.1 + 8 * Math.sin(i / 15))
  return {
    timestamps: Array.from({ length: count }, (_, i) => day(i)),
    open: close.map((c) => c - 0.5),
    high: close.map((c) => c + 1),
    low: close.map((c, i) => (i === 3 ? null : c - 1)),
    close,
    volume: close.map((_, i) => (i % 2 ? 1_000 + i : null)),
  }
}

async function run(code: string, data: BarColumns = bars(300)): Promise<PositionsOutcome> {
  const compiled = await compileStrategy(clang, code)
  if (!compiled.ok) return { ok: false, error: compiled.error, output: '' }
  return runStrategy(await WebAssembly.compile(compiled.wasm as BufferSource), data)
}

const strategy = (body: string) =>
  `#include <pyqxel.h>\n#include <cstdio>\n\nvoid strategy(const pyqxel::Bars& bars, std::span<double> positions) {\n${body}\n}\n`

describe('C++ strategies', () => {
  it(
    'runs the starter, a moving-average crossover, flat until its slow window fills',
    async () => {
      const outcome = await run(starterCode('cpp'))

      expect(outcome.ok).toBe(true)
      if (!outcome.ok) return
      expect(outcome.positions).toHaveLength(300)
      expect(outcome.positions.slice(0, 199).every((p) => p === 0)).toBe(true)
      expect(outcome.positions.slice(199)).toContain(1)
      expect(new Set(outcome.positions)).toEqual(new Set([0, 1]))
    },
    TIMEOUT,
  )

  it(
    'runs the commented stateful example',
    async () => {
      const starter = starterCode('cpp')
      const example = starter.slice(starter.indexOf('/*\n') + 3, starter.lastIndexOf('*/'))
      const withoutCrossover = starter.slice(0, starter.indexOf('// Long while'))

      const outcome = await run(withoutCrossover + example)

      expect(outcome.ok).toBe(true)
      if (!outcome.ok) return
      expect(outcome.positions).toContain(1)
      expect(outcome.positions.every((p) => p === 0 || p === 1)).toBe(true)
    },
    TIMEOUT,
  )

  it(
    'passes every field of every bar, NaN where missing and timestamps in UTC milliseconds',
    async () => {
      const data = bars(5)
      const outcome = await run(
        strategy(`  std::printf("%zu %.3f %.3f %.3f %.3f %d %d %.0f %lld\\n", bars.size,
      bars.open[1], bars.high[1], bars.low[1], bars.close[1],
      pyqxel::missing(bars.low[3]), pyqxel::missing(bars.volume[0]), bars.volume[1],
      static_cast<long long>(bars.timestamp[1]));`),
        data,
      )

      expect(outcome).toEqual({
        ok: true,
        positions: [0, 0, 0, 0, 0],
        output:
          `5 ${data.open[1]!.toFixed(3)} ${data.high[1]!.toFixed(3)} ` +
          `${data.low[1]!.toFixed(3)} ${data.close[1]!.toFixed(3)} 1 1 1001 ` +
          `${Date.parse(data.timestamps[1]!)}\n`,
      })
    },
    TIMEOUT,
  )

  it(
    'reports a compile error on the line of the user’s code',
    async () => {
      const outcome = await run(strategy('  double x = 1.0\n  positions[0] = x;'))

      expect(outcome.ok).toBe(false)
      if (outcome.ok) return
      expect(outcome.error.kind).toBe('compile')
      expect(outcome.error.line).toBe(5)
      expect(outcome.error.message).toContain("expected ';'")
      expect(outcome.error.traceback).toContain('strategy.cpp:5:')
    },
    TIMEOUT,
  )

  it(
    'explains a missing or misdeclared strategy',
    async () => {
      const outcome = await run('#include <pyqxel.h>\nvoid strategy(const pyqxel::Bars& bars) {}\n')

      expect(outcome.ok).toBe(false)
      if (outcome.ok) return
      expect(outcome.error.kind).toBe('compile')
      expect(outcome.error.message).toBe(
        'Define void strategy(const pyqxel::Bars& bars, std::span<double> positions).',
      )
    },
    TIMEOUT,
  )

  it(
    'rejects exceptions, which are compiled out',
    async () => {
      const outcome = await run(strategy('  if (bars.size == 0) throw 1;'))

      expect(outcome).toMatchObject({ ok: false, error: { kind: 'compile', line: 5 } })
    },
    TIMEOUT,
  )

  it(
    'names the first bar with a NaN position',
    async () => {
      const outcome = await run(
        strategy('  for (std::size_t i = 2; i < bars.size; ++i) positions[i] = bars.low[i] * 0;'),
        bars(6),
      )

      expect(outcome).toMatchObject({
        ok: false,
        error: {
          kind: 'result',
          message: expect.stringContaining('NaN on 1 bars, the first on 2025-01-05'),
        },
      })
    },
    TIMEOUT,
  )

  it(
    'names the first bar with a position out of range',
    async () => {
      const outcome = await run(strategy('  positions[4] = 2.5;\n  positions[5] = -3;'), bars(6))

      expect(outcome).toMatchObject({
        ok: false,
        error: {
          kind: 'result',
          message: 'Positions must be between -2 and 2; 2.5 on 2025-01-06 is not.',
        },
      })
    },
    TIMEOUT,
  )

  it(
    'returns what stdout and stderr printed',
    async () => {
      const outcome = await run(
        strategy(
          '  std::printf("to stdout\\n");\n  std::fprintf(stderr, "to stderr, no newline");',
        ),
      )

      expect(outcome).toMatchObject({ ok: true, output: 'to stdout\nto stderr, no newline' })
    },
    TIMEOUT,
  )

  it(
    'keeps the compiler’s warnings for the output',
    async () => {
      const compiled = await compileStrategy(
        clang,
        strategy('  int x = 0;\n  if (x = 1) positions[0] = 1.0;'),
      )

      expect(compiled.ok).toBe(true)
      if (!compiled.ok) return
      expect(compiled.warnings).toContain(
        'strategy.cpp:6:9: warning: using the result of an assignment',
      )
    },
    TIMEOUT,
  )

  it(
    'turns exit() and crashes into runtime errors',
    async () => {
      const exited = await run('#include <cstdlib>\n' + strategy('  std::exit(3);'))
      const crashed = await run(strategy('  __builtin_trap();'))

      expect(exited).toMatchObject({
        ok: false,
        error: { kind: 'runtime', message: expect.stringContaining('exit(3)') },
      })
      expect(crashed).toMatchObject({
        ok: false,
        error: { kind: 'runtime', message: expect.stringContaining('The strategy crashed') },
      })
    },
    TIMEOUT,
  )

  it(
    'gives the strategy no filesystem',
    async () => {
      const outcome = await run(
        strategy(
          '  std::FILE* f = std::fopen("/etc/passwd", "r");\n  std::printf(f ? "opened" : "no file");',
        ),
      )

      expect(outcome).toMatchObject({ ok: true, output: 'no file' })
    },
    TIMEOUT,
  )
})

describe('compile errors', () => {
  it('prefers an error in the user’s code over one elsewhere', () => {
    const error = compileError(
      "pyqxel.h:3:1: note: declared here\nstrategy.cpp:12:7: error: use of undeclared identifier 'y'\n",
    )

    expect(error).toMatchObject({
      kind: 'compile',
      line: 12,
      message: "use of undeclared identifier 'y'",
    })
  })

  it('falls back to the first error without a line', () => {
    expect(compileError('wasm-ld: error: something else\n')).toMatchObject({
      line: null,
      message: 'something else',
    })
  })
})

describe('checkPositions', () => {
  const stamps = ['2026-10-14T00:00:00-04:00', '2026-10-15T00:00:00-04:00']

  it('accepts positions from -2 to 2', () => {
    expect(checkPositions(new Float64Array([-2, 2]), stamps)).toEqual({
      ok: true,
      positions: [-2, 2],
    })
  })

  it('rejects infinity as out of range', () => {
    expect(checkPositions(new Float64Array([0, Infinity]), stamps)).toEqual({
      ok: false,
      message: 'Positions must be between -2 and 2; Infinity on 2026-10-15 is not.',
    })
  })
})

/** `count` dates on which GOOG climbs and UNH slides, with UNH's low missing on day 2. */
function universe(count: number): UniverseColumns {
  const dates = Array.from({ length: count }, (_, i) =>
    new Date(Date.UTC(2025, 0, 2) + i * 86_400_000).toISOString().slice(0, 10),
  )
  const asset = (step: number, missingLow: number | null) => {
    const close = Array.from({ length: count }, (_, i) => 100 + step * i)
    return {
      open: close,
      high: close,
      low: close.map((c, i) => (i === missingLow ? null : c)),
      close,
      volume: close.map(() => null),
    }
  }
  return { symbols: ['GOOG', 'UNH'], dates, assets: [asset(0.5, null), asset(-0.2, 2)] }
}

async function runAcross(code: string, data: UniverseColumns): Promise<WeightsOutcome> {
  const compiled = await compileStrategy(clang, code, true)
  if (!compiled.ok) return { ok: false, error: compiled.error, output: '' }
  return runUniverseStrategy(await WebAssembly.compile(compiled.wasm as BufferSource), data)
}

const across = (body: string) =>
  `#include <pyqxel.h>\n#include <cstdio>\n\nvoid strategy(const pyqxel::Universe& universe, pyqxel::Weights weights) {\n${body}\n}\n`

describe('C++ strategies across several symbols', () => {
  it(
    'runs the starter, holding the stronger symbol from its first month end with history',
    async () => {
      const outcome = await runAcross(starterCode('cpp', 'several'), universe(300))

      expect(outcome.ok).toBe(true)
      if (!outcome.ok) return
      expect(outcome.weights).toHaveLength(300)
      expect(outcome.weights[0]).toEqual([0, 0])
      expect(outcome.weights.at(-1)).toEqual([1, 0])
      expect(outcome.weights.every((row) => row[1] === 0)).toBe(true)
    },
    TIMEOUT,
  )

  it(
    'passes each symbol’s name, bars and dates',
    async () => {
      const outcome = await runAcross(
        across(`  std::printf("%zu %zu %s %s %.1f %d %lld\\n", universe.size, universe.count,
      universe.symbols[0], universe.symbols[1], universe[1].close[1],
      pyqxel::missing(universe[1].low[2]), static_cast<long long>(universe.timestamp[1]));
  for (std::size_t i = 0; i < universe.size; ++i) weights(i, 1) = -0.5;`),
        universe(3),
      )

      expect(outcome).toEqual({
        ok: true,
        weights: [
          [0, -0.5],
          [0, -0.5],
          [0, -0.5],
        ],
        output: `3 2 GOOG UNH 99.8 1 ${Date.UTC(2025, 0, 3)}\n`,
      })
    },
    TIMEOUT,
  )

  it(
    'names the signature to define when the single-symbol one is written instead',
    async () => {
      const outcome = await runAcross(starterCode('cpp'), universe(3))

      expect(outcome).toMatchObject({
        ok: false,
        error: { kind: 'compile', message: expect.stringContaining('pyqxel::Universe') },
      })
    },
    TIMEOUT,
  )
})

describe('checkWeights', () => {
  const data = universe(2)

  it('rejects weights whose absolute sum is over 2, naming the date', () => {
    expect(checkWeights(new Float64Array([1, 1, 1.5, -1]), data)).toEqual({
      ok: false,
      message:
        'The absolute weights may add up to at most 2 on a bar; on 2025-01-03 they add up to 2.5.',
    })
  })

  it('names the symbol and date of a NaN', () => {
    expect(checkWeights(new Float64Array([0, 0, 0, NaN]), data)).toMatchObject({
      ok: false,
      message: expect.stringContaining('The weight of UNH on 2025-01-03 is NaN'),
    })
  })
})
