import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { OHLCVBar } from '../api/types'
import { alignUniverse } from './types'
import { WorkerRunner, type WorkerRequest, type WorkerResponse } from './workerRunner'

const bars: OHLCVBar[] = [
  { timestamp: '2026-10-15T00:00:00-04:00', open: 10, high: 11, low: null, close: 10.5, volume: 7 },
  { timestamp: '2026-10-16T00:00:00-04:00', close: 11 },
]

/** Stands in for a Web Worker: records what it is sent and replies when told to. */
class FakeWorker extends EventTarget {
  readonly sent: WorkerRequest[] = []
  terminated = false

  postMessage(message: WorkerRequest): void {
    this.sent.push(message)
  }

  terminate(): void {
    this.terminated = true
  }

  reply(message: WorkerResponse): void {
    this.dispatchEvent(new MessageEvent('message', { data: message }))
  }

  lastRunId(): number {
    const run = this.sent.findLast((message) => message.type === 'run')
    if (!run || run.type !== 'run') throw new Error('Nothing was run.')
    return run.id
  }
}

let workers: FakeWorker[]

/** Lets a queued run reach its worker: runs start a microtask after `run()` is called. */
const flush = () => vi.advanceTimersByTimeAsync(0)

const runner = () =>
  new WorkerRunner(
    'python',
    () => {
      const worker = new FakeWorker()
      workers.push(worker)
      return worker as unknown as Worker
    },
    { runMs: 30_000, loadMs: 180_000 },
  )

beforeEach(() => {
  workers = []
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('WorkerRunner', () => {
  it('creates no worker until it is first used', () => {
    const python = runner()
    expect(workers).toHaveLength(0)

    python.warm()

    expect(workers).toHaveLength(1)
    expect(workers[0]!.sent).toEqual([{ type: 'warm' }])
  })

  it('sends the bars as columns and returns the positions with the original timestamps', async () => {
    const python = runner()
    const result = python.run('code', bars)
    await flush()
    const worker = workers[0]!
    const id = worker.lastRunId()

    expect(worker.sent[0]).toEqual({
      type: 'run',
      id,
      code: 'code',
      bars: {
        timestamps: ['2026-10-15T00:00:00-04:00', '2026-10-16T00:00:00-04:00'],
        open: [10, null],
        high: [11, null],
        low: [null, null],
        close: [10.5, 11],
        volume: [7, null],
      },
    })
    worker.reply({ type: 'started', id })
    worker.reply({ type: 'result', id, outcome: { ok: true, positions: [1, 0], output: 'hi\n' } })

    await expect(result).resolves.toEqual({
      ok: true,
      timestamps: ['2026-10-15T00:00:00-04:00', '2026-10-16T00:00:00-04:00'],
      positions: [1, 0],
      output: 'hi\n',
    })
  })

  it('passes errors from the code through', async () => {
    const python = runner()
    const result = python.run('code', bars)
    await flush()
    const worker = workers[0]!
    const id = worker.lastRunId()
    const error = { kind: 'runtime', message: 'KeyError', line: 3, traceback: 'tb' } as const

    worker.reply({ type: 'result', id, outcome: { ok: false, error, output: '' } })

    await expect(result).resolves.toEqual({ ok: false, error, output: '' })
  })

  it('rejects a reply with the wrong number of positions', async () => {
    const python = runner()
    const result = python.run('code', bars)
    await flush()
    const worker = workers[0]!

    worker.reply({
      type: 'result',
      id: worker.lastRunId(),
      outcome: { ok: true, positions: [1], output: '' },
    })

    await expect(result).resolves.toMatchObject({ ok: false, error: { kind: 'result' } })
  })

  it('stops code that runs too long and starts a fresh worker for the next run', async () => {
    const python = runner()
    const stuck = python.run('while True: pass', bars)
    await flush()
    const first = workers[0]!
    first.reply({ type: 'started', id: first.lastRunId() })

    await vi.advanceTimersByTimeAsync(29_999)
    expect(first.terminated).toBe(false)
    await vi.advanceTimersByTimeAsync(1)

    await expect(stuck).resolves.toMatchObject({
      ok: false,
      error: { kind: 'timeout', message: expect.stringContaining('30 seconds') },
    })
    expect(first.terminated).toBe(true)

    const next = python.run('code', bars)

    await flush()
    await vi.advanceTimersByTimeAsync(0)
    expect(workers).toHaveLength(2)
    const second = workers[1]!
    second.reply({
      type: 'result',
      id: second.lastRunId(),
      outcome: { ok: true, positions: [0, 0], output: '' },
    })
    await expect(next).resolves.toMatchObject({ ok: true })
  })

  it('gives the runtime longer to load than the code to run', async () => {
    const python = runner()
    const loading = python.run('code', bars)
    await flush()
    const worker = workers[0]!

    await vi.advanceTimersByTimeAsync(60_000)
    expect(worker.terminated).toBe(false)
    await vi.advanceTimersByTimeAsync(120_000)

    await expect(loading).resolves.toMatchObject({ ok: false, error: { kind: 'load' } })
    expect(worker.terminated).toBe(true)
  })

  it('reports when the code starts, after the runtime has loaded', async () => {
    const python = runner()
    const onStart = vi.fn()
    const result = python.run('code', bars, { onStart })
    await flush()
    const worker = workers[0]!
    const id = worker.lastRunId()
    expect(onStart).not.toHaveBeenCalled()

    worker.reply({ type: 'started', id })
    expect(onStart).toHaveBeenCalledOnce()
    worker.reply({ type: 'result', id, outcome: { ok: true, positions: [1, 1], output: '' } })
    await result
  })

  it('cancels a run when its signal aborts', async () => {
    const python = runner()
    const controller = new AbortController()
    const result = python.run('code', bars, { signal: controller.signal })
    await flush()

    controller.abort()

    await expect(result).resolves.toMatchObject({ ok: false, error: { kind: 'cancelled' } })
    expect(workers[0]!.terminated).toBe(true)
  })

  it('runs one strategy at a time, in order', async () => {
    const python = runner()
    const first = python.run('first', bars)
    await flush()
    const second = python.run('second', bars)
    const worker = workers[0]!
    await vi.advanceTimersByTimeAsync(0)

    expect(worker.sent.map((message) => message.type === 'run' && message.code)).toEqual(['first'])
    worker.reply({
      type: 'result',
      id: worker.lastRunId(),
      outcome: { ok: true, positions: [1, 1], output: '' },
    })
    await first
    await vi.advanceTimersByTimeAsync(0)
    expect(worker.lastRunId()).toBe(2)
    worker.reply({
      type: 'result',
      id: 2,
      outcome: { ok: true, positions: [0, 0], output: '' },
    })

    await expect(second).resolves.toMatchObject({ ok: true, positions: [0, 0] })
  })

  it('ignores replies to other runs', async () => {
    const python = runner()
    const result = python.run('code', bars)
    await flush()
    const worker = workers[0]!
    const id = worker.lastRunId()

    worker.reply({ type: 'result', id: id + 7, outcome: { ok: true, positions: [], output: '' } })
    worker.reply({ type: 'result', id, outcome: { ok: true, positions: [1, 1], output: '' } })

    await expect(result).resolves.toMatchObject({ ok: true, positions: [1, 1] })
  })
})

describe('strategies across several symbols', () => {
  const goog: OHLCVBar[] = [
    { timestamp: '2026-10-14T00:00:00-04:00', close: 100 },
    { timestamp: '2026-10-15T00:00:00-04:00', close: 101, volume: 5 },
    { timestamp: '2026-10-16T00:00:00-04:00', close: 102 },
  ]
  // A Tokyo listing, a holiday on the 15th and a bad close on the 16th.
  const sony: OHLCVBar[] = [
    { timestamp: '2026-10-14T00:00:00+09:00', close: 20 },
    { timestamp: '2026-10-16T00:00:00+09:00', close: 0 },
  ]

  it('lines the symbols up on the exchange dates all of them have a valid close', () => {
    const universe = alignUniverse([
      { symbol: 'GOOG', bars: goog },
      { symbol: 'UNH', bars: goog.slice(1) },
    ])

    expect(universe).toEqual({
      symbols: ['GOOG', 'UNH'],
      dates: ['2026-10-15', '2026-10-16'],
      assets: [
        {
          open: [null, null],
          high: [null, null],
          low: [null, null],
          close: [101, 102],
          volume: [5, null],
        },
        {
          open: [null, null],
          high: [null, null],
          low: [null, null],
          close: [101, 102],
          volume: [5, null],
        },
      ],
    })
    expect(
      alignUniverse([
        { symbol: 'GOOG', bars: goog },
        { symbol: 'SONY', bars: sony },
      ]).dates,
    ).toEqual(['2026-10-14'])
  })

  it('sends the universe and returns a row of weights per date', async () => {
    const python = runner()
    const universe = alignUniverse([
      { symbol: 'GOOG', bars: goog },
      { symbol: 'UNH', bars: goog },
    ])
    const result = python.runUniverse('code', universe)
    await flush()
    const worker = workers[0]!
    const id = worker.lastRunId()
    expect(worker.sent[0]).toEqual({ type: 'run', id, code: 'code', bars: universe })

    worker.reply({ type: 'started', id })
    worker.reply({
      type: 'result',
      id,
      outcome: {
        ok: true,
        weights: [
          [1, 0],
          [0.5, 0.5],
          [0, 1],
        ],
        output: '',
      },
    })

    await expect(result).resolves.toEqual({
      ok: true,
      dates: universe.dates,
      weights: [
        [1, 0],
        [0.5, 0.5],
        [0, 1],
      ],
      output: '',
    })
  })

  it('rejects weights that do not cover every symbol and date', async () => {
    const python = runner()
    const universe = alignUniverse([
      { symbol: 'GOOG', bars: goog },
      { symbol: 'UNH', bars: goog },
    ])
    const result = python.runUniverse('code', universe)
    await flush()
    const id = workers[0]!.lastRunId()
    workers[0]!.reply({ type: 'result', id, outcome: { ok: true, weights: [[1]], output: '' } })

    await expect(result).resolves.toMatchObject({ ok: false, error: { kind: 'result' } })
  })
})
