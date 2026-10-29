import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fitCopula, getHoldings, saveHoldings, simulate } from '../api/endpoints'
import { PortfolioReviewCard } from '../components/PortfolioReviewCard'
import type { HoldingsResponse, SimulationRequest, SimulationResponse } from '../api/types'
import { ChartCard, Legend } from '../charts/ChartCard'
import { CopulaHeatmap, CopulaTable } from '../charts/CopulaHeatmap'
import { FanChart, FanTable } from '../charts/FanChart'
import { useChartTheme } from '../charts/theme'
import { ErrorNotice, Notice } from '../components/Notice'
import { SearchBox } from '../components/SearchBox'
import { Segmented } from '../components/Segmented'
import { Stat } from '../components/Stat'
import { money, number, percent, signedPercent, tone } from '../lib/format'

const MAX_HOLDINGS = 20

interface Row {
  symbol: string
  shares: string
}

const HORIZONS = [
  { value: '21', label: '1M' },
  { value: '63', label: '3M' },
  { value: '126', label: '6M' },
  { value: '252', label: '1Y' },
] as const

const DEPENDENCE = [
  { value: 'student_t', label: 'Student t' },
  { value: 'gaussian', label: 'Gaussian' },
  { value: 'empirical', label: 'Historical' },
] as const

const COPULA_PERIODS = [
  { value: '1y', label: '1Y' },
  { value: '2y', label: '2Y' },
  { value: '5y', label: '5Y' },
] as const

const toRows = (data: HoldingsResponse | undefined): Row[] =>
  (data?.holdings ?? []).map((holding) => ({
    symbol: holding.symbol,
    shares: String(holding.shares),
  }))

export function PortfolioPage() {
  const queryClient = useQueryClient()
  const holdings = useQuery({ queryKey: ['holdings'], queryFn: getHoldings })
  // Unsaved edits; null shows the saved holdings.
  const [draft, setDraft] = useState<Row[] | null>(null)
  const dirty = draft !== null
  const rows = draft ?? toRows(holdings.data)

  const save = useMutation({
    mutationFn: () =>
      saveHoldings(rows.map((row) => ({ symbol: row.symbol, shares: Number(row.shares) }))),
    onSuccess: (data) => {
      queryClient.setQueryData(['holdings'], data)
      setDraft(null)
    },
  })

  const edit = (next: Row[]) => setDraft(next)
  const invalid = rows.some((row) => !(Number(row.shares) > 0))
  const valued = new Map((holdings.data?.holdings ?? []).map((h) => [h.symbol, h]))

  const priced = (holdings.data?.holdings ?? []).filter(
    (holding) => holding.weight !== null && holding.weight !== undefined && holding.weight > 0,
  )

  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>Portfolio</h1>
          <div className="subtitle">
            Your holdings, how they could fare, and how they move together.
          </div>
        </div>
      </header>

      <section className="card">
        <div className="card-header">
          <div>
            <h2>Holdings</h2>
            <div className="subtitle">
              Valued at the latest prices · {money(holdings.data?.total_value)} in total
            </div>
          </div>
          <div className="filter-row">
            {dirty && (
              <button type="button" className="button ghost" onClick={() => setDraft(null)}>
                Discard
              </button>
            )}
            <button
              type="button"
              className="button primary"
              disabled={!dirty || invalid || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
        {holdings.error !== null && <ErrorNotice error={holdings.error} />}
        {save.error !== null && <ErrorNotice error={save.error} />}
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Name</th>
                <th className="num">Shares</th>
                <th className="num">Price</th>
                <th className="num">Value</th>
                <th className="num" style={{ minWidth: 150 }}>
                  Weight
                </th>
                <th>
                  <span className="visually-hidden">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => {
                const value = dirty ? undefined : valued.get(row.symbol)
                return (
                  <tr key={row.symbol}>
                    <td>
                      <Link to={`/stocks/${encodeURIComponent(row.symbol)}`}>{row.symbol}</Link>
                    </td>
                    <td className="muted">{valued.get(row.symbol)?.name ?? ''}</td>
                    <td className="num">
                      <input
                        type="number"
                        min="0"
                        step="any"
                        aria-label={`Shares of ${row.symbol}`}
                        style={{ width: 110, textAlign: 'right' }}
                        value={row.shares}
                        aria-invalid={!(Number(row.shares) > 0)}
                        onChange={(event) =>
                          edit(
                            rows.map((r, i) =>
                              i === index ? { ...r, shares: event.target.value } : r,
                            ),
                          )
                        }
                      />
                    </td>
                    <td className="num">{money(valued.get(row.symbol)?.price)}</td>
                    <td className="num">{money(value?.value)}</td>
                    <td className="num">
                      <WeightBar weight={value?.weight} />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="button ghost small danger"
                        onClick={() => edit(rows.filter((_, i) => i !== index))}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {rows.length === 0 && !holdings.isLoading && (
          <div className="empty">No holdings yet. Add your first one below.</div>
        )}
        <div className="filter-row" style={{ marginTop: 14 }}>
          <div style={{ flex: 1, maxWidth: 360 }}>
            <SearchBox
              clearOnSelect
              label="Add a holding"
              placeholder={
                rows.length >= MAX_HOLDINGS ? `At most ${MAX_HOLDINGS} holdings` : 'Add a holding'
              }
              onSelect={(symbol) => {
                if (rows.length >= MAX_HOLDINGS || rows.some((row) => row.symbol === symbol)) {
                  return
                }
                edit([...rows, { symbol, shares: '' }])
              }}
            />
          </div>
          {dirty && <span className="muted">Enter share counts, then save.</span>}
        </div>
        {holdings.data?.notice && !dirty && (
          <div style={{ marginTop: 12 }}>
            <Notice tone="warning">{holdings.data.notice}</Notice>
          </div>
        )}
      </section>

      <SimulationCard
        holdings={priced.map((h) => ({ symbol: h.symbol, weight: h.weight! }))}
        initialValue={holdings.data?.total_value ?? 0}
        stale={dirty}
      />
      <CopulaCard symbols={priced.map((h) => h.symbol)} stale={dirty} />
      <PortfolioReviewCard hasHoldings={priced.length > 0} stale={dirty} />
    </div>
  )
}

function WeightBar({ weight }: { weight: number | null | undefined }) {
  if (weight === null || weight === undefined) return <span className="muted">—</span>
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <span
        aria-hidden="true"
        style={{
          display: 'inline-block',
          width: 80,
          height: 6,
          borderRadius: 3,
          background: 'var(--surface-sunken)',
          overflow: 'hidden',
        }}
      >
        <span
          style={{
            display: 'block',
            width: `${Math.min(100, weight * 100)}%`,
            height: '100%',
            borderRadius: 3,
            background: 'var(--series-1)',
          }}
        />
      </span>
      <span style={{ minWidth: 48, textAlign: 'right' }}>{percent(weight)}</span>
    </span>
  )
}

/** Normalizes rounding so weights sum to exactly one, as the API requires. */
function normalized(holdings: { symbol: string; weight: number }[]) {
  const total = holdings.reduce((sum, h) => sum + h.weight, 0)
  return holdings.map((h) => ({ symbol: h.symbol, weight: h.weight / total }))
}

function SimulationCard({
  holdings,
  initialValue,
  stale,
}: {
  holdings: { symbol: string; weight: number }[]
  initialValue: number
  stale: boolean
}) {
  const theme = useChartTheme()
  const [horizon, setHorizon] = useState<(typeof HORIZONS)[number]['value']>('63')
  const [dependence, setDependence] = useState<(typeof DEPENDENCE)[number]['value']>('student_t')
  const run = useMutation({
    mutationFn: (request: SimulationRequest) => simulate(request),
  })
  const result: SimulationResponse | undefined = run.data
  const summary = result?.simulation
  const risk95 = summary?.risk.find((r) => Math.abs(r.confidence - 0.95) < 1e-9)
  const drawdown = summary?.max_drawdown.percentiles.find((p) => p.percentile === 50)

  const start = initialValue > 0 ? initialValue : 10_000
  const disabled = holdings.length === 0 || stale || run.isPending

  return (
    <ChartCard
      title="Simulated outcomes"
      subtitle={
        summary
          ? `${summary.paths.toLocaleString()} paths over ${summary.horizon} trading days, fitted on ${result.period} of history`
          : 'Monte Carlo paths from the holdings’ historical returns and how they co-move'
      }
      actions={
        <>
          <Segmented label="Horizon" value={horizon} options={HORIZONS} onChange={setHorizon} />
          <Segmented
            label="Dependence"
            value={dependence}
            options={DEPENDENCE}
            onChange={setDependence}
          />
          <button
            type="button"
            className="button primary"
            disabled={disabled}
            onClick={() =>
              run.mutate({
                holdings: normalized(holdings),
                horizon: Number(horizon),
                dependence,
                paths: 10_000,
                initial_value: start,
                period: '5y',
              })
            }
          >
            {run.isPending ? 'Simulating…' : summary ? 'Run again' : 'Run simulation'}
          </button>
        </>
      }
      loading={run.isPending && !summary}
      refreshing={run.isPending}
      error={run.error ?? undefined}
      empty={
        holdings.length === 0
          ? 'Save holdings with prices to simulate them.'
          : stale
            ? 'Save your changes to simulate the new holdings.'
            : 'Run a simulation to see the range of outcomes.'
      }
      legend={
        <Legend
          items={[
            { label: 'Median', color: theme.seq600 },
            { label: 'Middle 50%', color: theme.seq200, shape: 'box' },
            { label: '90% range', color: theme.seq100, shape: 'box' },
          ]}
        />
      }
      chart={summary && <FanChart fan={summary.fan_chart} initial={summary.initial_value} />}
      table={summary && <FanTable fan={summary.fan_chart} />}
      footer={
        summary && (
          <div className="stack" style={{ gap: 12, marginTop: 14 }}>
            <div className="stats">
              <Stat
                label="Expected return"
                value={signedPercent(summary.expected_return)}
                className={tone(summary.expected_return)}
              />
              <Stat label="Median return" value={signedPercent(summary.median_return)} />
              <Stat label="Chance of a loss" value={percent(summary.probability_of_loss, 0)} />
              <Stat
                label="95% Value at Risk"
                value={money(risk95?.value_at_risk_amount, 'USD', 0)}
                detail={`${percent(risk95?.value_at_risk)} of value`}
              />
              <Stat
                label="95% Expected shortfall"
                value={money(risk95?.conditional_value_at_risk_amount, 'USD', 0)}
                detail="Average loss in the worst 5%"
              />
              <Stat
                label="Median max drawdown"
                value={percent(drawdown?.value)}
                detail={
                  summary.copula_degrees_of_freedom
                    ? `Copula t, ${number(summary.copula_degrees_of_freedom, 1)} d.f.`
                    : undefined
                }
              />
            </div>
            {(summary.warnings ?? []).map((warning) => (
              <Notice key={warning} tone="warning">
                {warning}
              </Notice>
            ))}
            {result.notice && <Notice tone="warning">{result.notice}</Notice>}
            <p className="disclaimer">{result.disclaimer}</p>
          </div>
        )
      }
    />
  )
}

function CopulaCard({ symbols, stale }: { symbols: string[]; stale: boolean }) {
  const [period, setPeriod] = useState<(typeof COPULA_PERIODS)[number]['value']>('5y')
  const key = useMemo(() => [...symbols].sort().join(','), [symbols])
  const fit = useQuery({
    queryKey: ['copula', key, period],
    queryFn: () => fitCopula(symbols, period),
    enabled: symbols.length >= 2 && !stale,
    placeholderData: keepPreviousData,
    staleTime: 30 * 60_000,
  })
  const data = fit.data
  return (
    <ChartCard
      title="How the holdings move together"
      subtitle={
        data
          ? `Kendall's tau on ${data.observations.toLocaleString()} common trading days · best fit: ${data.preferred === 'student_t' ? `Student t copula, ${number(data.student_t.degrees_of_freedom, 1)} d.f.` : 'Gaussian copula'}`
          : "Kendall's rank correlation between each pair of holdings"
      }
      actions={
        <Segmented label="History" value={period} options={COPULA_PERIODS} onChange={setPeriod} />
      }
      loading={fit.isLoading}
      refreshing={fit.isPlaceholderData}
      error={fit.error ?? undefined}
      empty={
        symbols.length < 2
          ? 'Save at least two priced holdings to see how they move together.'
          : 'Save your changes to update.'
      }
      chart={data && <CopulaHeatmap fit={data} />}
      table={data && <CopulaTable fit={data} />}
      footer={
        data && (
          <div className="stack" style={{ gap: 12, marginTop: 14 }}>
            <p className="disclaimer">
              Tail dependence is the chance two holdings crash together, beyond what their
              correlation implies; the Student t copula captures it and the Gaussian does not.
            </p>
            {[...(data.warnings ?? []), ...(data.notice ? [data.notice] : [])].map((warning) => (
              <Notice key={warning} tone="warning">
                {warning}
              </Notice>
            ))}
          </div>
        )
      }
    />
  )
}
