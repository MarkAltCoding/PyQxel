// Pieces the single-stock and cross-sectional backtests share.
import type { ReactNode } from 'react'
import type { BacktestResponse, PerformanceMetrics } from '../api/types'
import { FACTOR_NAMES } from '../lib/factors'
import { number, percent, shortDate, signedPercent } from '../lib/format'

export function NumberField(props: {
  label: string
  value: string
  onChange: (value: string) => void
  min: number
  max: number
  step?: number
}) {
  return (
    <label className="field" style={{ width: 120 }}>
      <span>{props.label}</span>
      <input
        type="number"
        required
        min={props.min}
        max={props.max}
        step={props.step ?? 1}
        value={props.value}
        onChange={(event) => props.onChange(event.target.value)}
      />
    </label>
  )
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <tr>
      <td>{label}</td>
      <td className="num">{children}</td>
    </tr>
  )
}

export function MetricsTable({
  strategy,
  benchmark,
  benchmarkLabel = 'Buy and hold',
}: {
  strategy: PerformanceMetrics
  benchmark: PerformanceMetrics
  benchmarkLabel?: string
}) {
  const rows: [string, (m: PerformanceMetrics) => string][] = [
    ['Total return', (m) => signedPercent(m.total_return)],
    ['Annualized return', (m) => signedPercent(m.annualized_return)],
    ['Annualized volatility', (m) => percent(m.annualized_volatility)],
    ['Sharpe ratio', (m) => number(m.sharpe_ratio)],
    ['Sortino ratio', (m) => number(m.sortino_ratio)],
    ['Max drawdown', (m) => percent(m.max_drawdown)],
    ['Drawdown trough', (m) => shortDate(m.max_drawdown_trough)],
    [
      'Recovered',
      (m) => (m.max_drawdown_recovery ? shortDate(m.max_drawdown_recovery) : 'Not yet'),
    ],
  ]
  return (
    <table className="data">
      <thead>
        <tr>
          <th />
          <th className="num">Strategy</th>
          <th className="num">{benchmarkLabel}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, format]) => (
          <tr key={label}>
            <td>{label}</td>
            <td className="num">{format(strategy)}</td>
            <td className="num">{format(benchmark)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** The strategy's alpha and factor betas, in a card. */
export function AttributionCard({ attribution }: { attribution: BacktestResponse['attribution'] }) {
  return (
    <section className="card">
      <div className="card-header">
        <div>
          <h2>Return attribution</h2>
          <div className="subtitle">The strategy's daily returns regressed on factor returns</div>
        </div>
      </div>
      {attribution ? (
        <table className="data">
          <tbody>
            <Row label="Alpha (annualized)">
              {signedPercent(attribution.fit.alpha.estimate)} (t{' '}
              {number(attribution.fit.alpha.t_stat)})
            </Row>
            {attribution.fit.exposures.map((exposure) => (
              <Row key={exposure.factor} label={`${FACTOR_NAMES[exposure.factor]} beta`}>
                {number(exposure.estimate)}
              </Row>
            ))}
            <Row label="R²">{percent(attribution.fit.r_squared, 0)}</Row>
          </tbody>
        </table>
      ) : (
        <div className="empty">No attribution for this backtest.</div>
      )}
    </section>
  )
}
