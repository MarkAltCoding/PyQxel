// Pieces the single-stock and cross-sectional backtests share.
import type { ReactNode } from 'react'
import type { BacktestResponse, PerformanceMetrics } from '../api/types'
import { FACTOR_INFO, FACTOR_NAMES } from '../lib/factors'
import type { InfoKey } from '../lib/glossary'
import { InfoTip } from './InfoTip'
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

export function Row({
  label,
  info,
  children,
}: {
  label: string
  info: InfoKey
  children: ReactNode
}) {
  return (
    <tr>
      <td>
        {label}
        <InfoTip topic={info} />
      </td>
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
  const rows: [string, InfoKey, (m: PerformanceMetrics) => string][] = [
    ['Total return', 'totalReturn', (m) => signedPercent(m.total_return)],
    ['Annualized return', 'annualizedReturn', (m) => signedPercent(m.annualized_return)],
    ['Annualized volatility', 'annualizedVolatility', (m) => percent(m.annualized_volatility)],
    ['Sharpe ratio', 'sharpe', (m) => number(m.sharpe_ratio)],
    ['Sortino ratio', 'sortino', (m) => number(m.sortino_ratio)],
    ['Max drawdown', 'maxDrawdown', (m) => percent(m.max_drawdown)],
    ['Drawdown trough', 'drawdownTrough', (m) => shortDate(m.max_drawdown_trough)],
    [
      'Recovered',
      'recovered',
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
        {rows.map(([label, info, format]) => (
          <tr key={label}>
            <td>
              {label}
              <InfoTip topic={info} />
            </td>
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
          <div className="heading-with-info">
            <h2>Return attribution</h2>
            <InfoTip topic="attribution" />
          </div>
          <div className="subtitle">The strategy's daily returns regressed on factor returns</div>
        </div>
      </div>
      {attribution ? (
        <table className="data">
          <tbody>
            <Row label="Alpha (annualized)" info="alpha">
              {signedPercent(attribution.fit.alpha.estimate)} (t{' '}
              {number(attribution.fit.alpha.t_stat)})
            </Row>
            {attribution.fit.exposures.map((exposure) => (
              <Row
                key={exposure.factor}
                label={`${FACTOR_NAMES[exposure.factor]} beta`}
                info={FACTOR_INFO[exposure.factor]}
              >
                {number(exposure.estimate)}
              </Row>
            ))}
            <Row label="R²" info="rSquared">
              {percent(attribution.fit.r_squared, 0)}
            </Row>
          </tbody>
        </table>
      ) : (
        <div className="empty">No attribution for this backtest.</div>
      )}
    </section>
  )
}
