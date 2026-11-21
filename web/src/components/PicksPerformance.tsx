import { useQuery } from '@tanstack/react-query'
import { getPicksPerformance, getSavedPicks } from '../api/endpoints'
import type { EquityPoint, PicksBenchmark, PicksPerformance } from '../api/types'
import { ChartCard, Legend } from '../charts/ChartCard'
import { EquityChart, EquityTable } from '../charts/EquityChart'
import { useChartTheme } from '../charts/theme'
import { dateTime, money, percent, shortDate, signedPercent, tone } from '../lib/format'
import { modelName } from '../lib/modelName'
import { PICKS_BENCHMARKS, PICKS_LIST_KEY } from '../lib/picks'
import { InfoTip } from './InfoTip'
import { ErrorNotice, Notice } from './Notice'
import { Segmented } from './Segmented'
import { Stat } from './Stat'

/** The benchmark choice both cards share. */
export function BenchmarkChoice({
  label,
  value,
  onChange,
}: {
  label: string
  value: PicksBenchmark
  onChange: (benchmark: PicksBenchmark) => void
}) {
  return <Segmented label={label} value={value} options={PICKS_BENCHMARKS} onChange={onChange} />
}

/** A list's gain since it was made, as the backtest's equity chart draws it. */
function points(performance: PicksPerformance): EquityPoint[] {
  return (performance.series ?? []).map((point) => ({
    timestamp: point.date,
    strategy: 1 + (point.picks ?? 0),
    benchmark: 1 + (point.benchmark ?? Number.NaN),
  })) as EquityPoint[]
}

function trackingNote(performance: { ended: boolean; ends_on: string }): string {
  return performance.ended
    ? `Holding period ended ${shortDate(performance.ends_on)}; tracking stopped then`
    : `Tracked until ${shortDate(performance.ends_on)}, when the holding period ends`
}

/**
 * How one stored list of picks has done since it was made: the list against the
 * benchmark, day by day, and each pick's gain.
 */
export function PicksPerformanceCard({
  id,
  benchmark,
  onBenchmark,
}: {
  id: string
  benchmark: PicksBenchmark
  onBenchmark: (benchmark: PicksBenchmark) => void
}) {
  const theme = useChartTheme()
  const query = useQuery({
    queryKey: ['picks', 'performance', id, benchmark],
    queryFn: () => getPicksPerformance(id, benchmark),
  })
  const data = query.data
  const short = PICKS_BENCHMARKS.find((option) => option.value === benchmark)?.label ?? benchmark
  return (
    <ChartCard
      id="picks-performance"
      title="How these picks have done"
      info="picksPerformance"
      subtitle={
        data
          ? `${data.start ? `Since the close of ${shortDate(data.start)}` : 'Since they were made'} · ${trackingNote(data)}`
          : undefined
      }
      actions={
        <BenchmarkChoice
          label="Compare these picks with"
          value={benchmark}
          onChange={onBenchmark}
        />
      }
      loading={query.isLoading}
      refreshing={query.isFetching && !query.isLoading}
      error={query.error}
      empty={
        data && (data.series ?? []).length === 0 ? 'No prices since the picks were made yet.' : null
      }
      legend={
        <Legend
          items={[
            { label: 'Claude’s Picks', color: theme.series1 },
            { label: data?.benchmark_name ?? short, color: theme.series2 },
          ]}
        />
      }
      chart={
        data && (data.series ?? []).length > 0 ? (
          <EquityChart
            points={points(data)}
            strategyLabel="Claude’s Picks"
            benchmarkLabel={data.benchmark_name}
            benchmarkShort={short}
          />
        ) : undefined
      }
      table={
        data && (data.series ?? []).length > 0 ? (
          <EquityTable
            points={points(data)}
            strategyLabel="Claude’s Picks"
            benchmarkLabel={data.benchmark_name}
          />
        ) : undefined
      }
      footer={
        data && (
          <div className="stack" style={{ gap: 12, marginTop: 14 }}>
            <div className="stats">
              <Stat
                label="Picks’ return"
                info="picksReturn"
                value={signedPercent(data.total_return)}
                className={tone(data.total_return)}
              />
              <Stat
                label={`${short}’s return`}
                info="picksBenchmarkReturn"
                value={signedPercent(data.benchmark_return)}
              />
              <Stat
                label="Versus the benchmark"
                info="picksExcess"
                value={signedPercent(data.excess_return)}
                className={tone(data.excess_return)}
              />
              <Stat
                label={data.ended ? 'Tracked until (ended)' : 'Tracked until'}
                info="picksTracking"
                value={shortDate(data.ends_on)}
              />
            </div>
            {data.notice && <Notice tone="warning">{data.notice}</Notice>}
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Pick</th>
                    <th className="num">Weight</th>
                    <th className="num">Start</th>
                    <th className="num">Latest</th>
                    <th className="num">
                      <span className="heading-with-info">
                        Gain
                        <InfoTip topic="pickGain" />
                      </span>
                    </th>
                    <th className="num">Added to the list</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.picks ?? []).map((pick) => (
                    <tr key={pick.symbol}>
                      <td>{pick.symbol}</td>
                      <td className="num">{percent(pick.weight, 0)}</td>
                      <td className="num">{money(pick.start_price)}</td>
                      <td className="num">{money(pick.latest_price)}</td>
                      <td className={`num ${tone(pick.gain) ?? ''}`}>{signedPercent(pick.gain)}</td>
                      <td className={`num ${tone(pick.contribution) ?? ''}`}>
                        {signedPercent(pick.contribution)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      }
    />
  )
}

/**
 * Every stored list of picks, newest first, with how each has done, as the backtester
 * lists its saved backtests. A row opens that list in full.
 */
export function SavedPicksCard({
  openedId,
  onOpen,
  onDelete,
  benchmark,
  onBenchmark,
}: {
  /** The list shown above, marked in the table. */
  openedId: string | null
  onOpen: (id: string) => void
  onDelete: (id: string) => void
  benchmark: PicksBenchmark
  onBenchmark: (benchmark: PicksBenchmark) => void
}) {
  const saved = useQuery({
    queryKey: [...PICKS_LIST_KEY, 'performance', benchmark],
    queryFn: () => getSavedPicks(benchmark),
  })
  const items = saved.data?.items ?? []
  const short = PICKS_BENCHMARKS.find((option) => option.value === benchmark)?.label ?? benchmark
  return (
    <section id="saved-picks" className="card" aria-busy={saved.isFetching}>
      <div className="card-header">
        <div className="heading-with-info">
          <h2>Saved Claude’s Picks</h2>
          <InfoTip topic="savedPicks" />
        </div>
        <BenchmarkChoice
          label="Compare saved picks with"
          value={benchmark}
          onChange={onBenchmark}
        />
      </div>
      {saved.error !== null && <ErrorNotice error={saved.error} />}
      {saved.data?.notice && <Notice tone="warning">{saved.data.notice}</Notice>}
      {saved.isLoading && (
        <div className="empty">
          <div className="spinner" style={{ margin: '0 auto' }} />
        </div>
      )}
      {saved.data && items.length === 0 && (
        <div className="empty">Lists of picks Claude makes for you are saved here.</div>
      )}
      {items.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Made</th>
                <th>Held for</th>
                <th>Picks</th>
                <th>Written by</th>
                <th className="num">
                  <span className="heading-with-info">
                    Return
                    <InfoTip topic="picksReturn" />
                  </span>
                </th>
                <th className="num">
                  <span className="heading-with-info">
                    {short}
                    <InfoTip topic="picksBenchmarkReturn" />
                  </span>
                </th>
                <th className="num">
                  <span className="heading-with-info">
                    Versus
                    <InfoTip topic="picksExcess" />
                  </span>
                </th>
                <th>
                  <span className="heading-with-info">
                    Tracked until
                    <InfoTip topic="picksTracking" />
                  </span>
                </th>
                <th>
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, index) => (
                <tr
                  key={item.id}
                  className="clickable"
                  aria-current={
                    item.id === openedId || (openedId === null && index === 0) ? 'true' : undefined
                  }
                  onClick={() => onOpen(item.id)}
                >
                  <td>
                    <button
                      type="button"
                      className="link-button"
                      onClick={(event) => {
                        event.stopPropagation()
                        onOpen(item.id)
                      }}
                    >
                      {dateTime(item.generated_at)}
                    </button>
                  </td>
                  <td>
                    {item.horizon}
                    {item.risk_tolerance ? ` · ${item.risk_tolerance}` : ''}
                  </td>
                  <td>{item.symbols.join(', ')}</td>
                  <td>{modelName(item.model)}</td>
                  <td className={`num ${tone(item.total_return) ?? ''}`}>
                    {signedPercent(item.total_return)}
                  </td>
                  <td className="num">{signedPercent(item.benchmark_return)}</td>
                  <td className={`num ${tone(item.excess_return) ?? ''}`}>
                    {signedPercent(item.excess_return)}
                  </td>
                  <td className="muted">
                    {shortDate(item.ends_on)}
                    {item.ended ? ' (ended)' : ''}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="button ghost small danger"
                      onClick={(event) => {
                        event.stopPropagation()
                        onDelete(item.id)
                      }}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
