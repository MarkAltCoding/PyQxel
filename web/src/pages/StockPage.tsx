import { useState } from 'react'
import { useParams } from 'react-router'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import { getFactors, getHistory, getTicker, getVolatility } from '../api/endpoints'
import type { FactorRegression, VolatilityEstimate } from '../api/types'
import { ChartCard, Legend } from '../charts/ChartCard'
import { FactorChart, FactorTable } from '../charts/FactorChart'
import { PriceChart, PriceTable } from '../charts/PriceChart'
import { useChartTheme } from '../charts/theme'
import { VolatilityChart, VolatilityTable } from '../charts/VolatilityChart'
import { AlternativeCard } from '../components/AlternativeCard'
import { AnalysisPanel } from '../components/AnalysisPanel'
import { MetricsCard } from '../components/MetricsCard'
import { NewsCard } from '../components/NewsCard'
import { SignalsCard } from '../components/SignalsCard'
import { ErrorNotice, Notice } from '../components/Notice'
import { Segmented } from '../components/Segmented'
import { Stat } from '../components/Stat'
import { InfoTip } from '../components/InfoTip'
import { compact, money, number, percent, shortDate, signedPercent, tone } from '../lib/format'

const PERIODS = [
  { value: '1y', label: '1Y' },
  { value: '2y', label: '2Y' },
  { value: '5y', label: '5Y' },
  { value: '10y', label: '10Y' },
] as const
type Period = (typeof PERIODS)[number]['value']

const FACTOR_MODELS = [
  { value: 'ff3', label: 'FF3' },
  { value: 'carhart4', label: 'Carhart' },
  { value: 'ff5', label: 'FF5' },
] as const
type FactorModel = (typeof FACTOR_MODELS)[number]['value']

/** Daily bars up to two years; weekly beyond, so long charts stay light. */
const intervalFor = (period: Period) => (period === '1y' || period === '2y' ? '1d' : '1wk')

interface VolatilityResult {
  estimate: VolatilityEstimate
  /** Set when GARCH was unavailable (no R on the server) and EWMA was used instead. */
  fellBack: boolean
}

async function loadVolatility(symbol: string, period: Period): Promise<VolatilityResult> {
  try {
    return { estimate: await getVolatility(symbol, period, 'garch'), fellBack: false }
  } catch (error) {
    if (error instanceof ApiError && error.status === 503) {
      return { estimate: await getVolatility(symbol, period, 'ewma'), fellBack: true }
    }
    throw error
  }
}

export function StockPage() {
  const symbol = (useParams().symbol ?? '').toUpperCase()
  const [period, setPeriod] = useState<Period>('1y')
  const [factorModel, setFactorModel] = useState<FactorModel>('carhart4')
  const theme = useChartTheme()

  const ticker = useQuery({ queryKey: ['ticker', symbol], queryFn: () => getTicker(symbol) })
  const interval = intervalFor(period)
  const history = useQuery({
    queryKey: ['history', symbol, period, interval],
    queryFn: () => getHistory(symbol, period, interval),
    placeholderData: keepPreviousData,
  })
  const factors = useQuery({
    queryKey: ['factors', symbol, period, factorModel],
    queryFn: () => getFactors(symbol, period, factorModel),
    placeholderData: keepPreviousData,
  })
  // GARCH needs about four years of daily returns to estimate reliably (two years often
  // leave it with no volatility clustering and a flat line), so shorter periods are fitted
  // on five years and only the selected period is drawn.
  const volatilityPeriod: Period = period === '1y' || period === '2y' ? '5y' : period
  const volatility = useQuery({
    queryKey: ['volatility', symbol, volatilityPeriod],
    queryFn: () => loadVolatility(symbol, volatilityPeriod),
    placeholderData: keepPreviousData,
  })

  const info = ticker.data
  const currency = info?.currency ?? 'USD'
  const bars = history.data?.bars ?? []
  const first = bars[0]?.close
  const last = bars.at(-1)?.close
  const periodReturn = first && last ? last / first - 1 : null

  if (ticker.error instanceof ApiError && ticker.error.status === 404) {
    return (
      <div className="stack">
        <h1>{symbol}</h1>
        <ErrorNotice error={ticker.error} />
      </div>
    )
  }

  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>
            {symbol}
            {info?.name && (
              <span style={{ fontWeight: 400, color: 'var(--text-secondary)' }}>
                {' '}
                · {info.name}
              </span>
            )}
          </h1>
          <div className="subtitle">
            {[info?.exchange, info?.sector, info?.industry].filter(Boolean).join(' · ') ||
              (ticker.isLoading ? 'Loading…' : '')}
          </div>
        </div>
        <Segmented label="Period" value={period} options={PERIODS} onChange={setPeriod} />
      </header>

      {ticker.error !== null && <ErrorNotice error={ticker.error} />}
      {info?.quote_type === 'MONEYMARKET' && (
        <Notice>
          <span className="heading-with-info">
            <span>
              {symbol} is a money-market fund: it holds a steady $1 a share and pays its return as
              interest, so price charts, volatility and factor models have little to show. In a
              portfolio it counts as cash.
            </span>
            <InfoTip topic="moneyMarketCash" />
          </span>
        </Notice>
      )}

      <div className="stats">
        <Stat info="price" label="Price" value={money(info?.price, currency)} />
        <Stat info="marketCap" label="Market cap" value={compact(info?.market_cap)} />
        <Stat
          info="periodReturn"
          label={`Return, ${period.toUpperCase()}`}
          value={signedPercent(periodReturn)}
          className={tone(periodReturn)}
          detail={bars.length ? `Since ${shortDate(bars[0]?.timestamp.slice(0, 10))}` : undefined}
        />
        <Stat
          info="volatilityNow"
          label="Volatility now"
          value={percent(volatility.data?.estimate.fit.current_volatility)}
          detail="Annualized, conditional"
        />
        <Stat
          info="marketBeta"
          label="Market beta"
          value={number(
            factors.data?.fit.exposures.find((row) => row.factor === 'Mkt-RF')?.estimate,
          )}
          detail={factors.data ? `R² ${percent(factors.data.fit.r_squared, 0)}` : undefined}
        />
      </div>

      <ChartCard
        id="price"
        info="priceChart"
        title="Price"
        subtitle={`Adjusted closes, ${interval === '1d' ? 'daily' : 'weekly'} · Ctrl + scroll to zoom`}
        loading={history.isLoading}
        refreshing={history.isPlaceholderData}
        error={history.error ?? undefined}
        chart={bars.length > 0 && <PriceChart bars={bars} currency={currency} />}
        table={<PriceTable bars={bars} currency={currency} />}
        empty="No price history."
        footer={history.data?.notice && <Notice tone="warning">{history.data.notice}</Notice>}
      />

      <div className="grid-2">
        <FactorCard
          regression={factors.data}
          loading={factors.isLoading}
          refreshing={factors.isPlaceholderData}
          error={factors.error}
          model={factorModel}
          onModel={setFactorModel}
        />
        <ChartCard
          id="volatility"
          info="volatilityChart"
          title="Volatility"
          subtitle={
            volatility.data
              ? `${volatility.data.estimate.fit.model === 'garch' ? 'GARCH(1,1)' : 'EWMA'} fitted on ${volatilityPeriod.toUpperCase()}, annualized, with a ${volatility.data.estimate.fit.forecast.length}-day forecast`
              : 'Annualized conditional volatility'
          }
          loading={volatility.isLoading}
          refreshing={volatility.isPlaceholderData}
          error={volatility.error ?? undefined}
          legend={
            <Legend
              items={[
                { label: 'Fitted', color: theme.series1 },
                { label: 'Forecast', color: theme.series1, shape: 'dashed' },
              ]}
            />
          }
          chart={
            volatility.data && (
              <VolatilityChart
                fit={volatility.data.estimate.fit}
                since={bars[0]?.timestamp.slice(0, 10)}
              />
            )
          }
          table={volatility.data && <VolatilityTable fit={volatility.data.estimate.fit} />}
          footer={
            volatility.data && (
              <VolatilityFooter
                estimate={volatility.data.estimate}
                fellBack={volatility.data.fellBack}
              />
            )
          }
        />
      </div>

      <NewsCard key={`news-${symbol}`} symbol={symbol} />

      <SignalsCard symbol={symbol} currency={currency} />

      <MetricsCard symbol={symbol} currency={currency} />

      <AlternativeCard symbol={symbol} />

      <AnalysisPanel key={symbol} symbol={symbol} />
    </div>
  )
}

function FactorCard(props: {
  regression: FactorRegression | undefined
  loading: boolean
  refreshing: boolean
  error: unknown
  model: FactorModel
  onModel: (model: FactorModel) => void
}) {
  const fit = props.regression?.fit
  return (
    <ChartCard
      id="factors"
      info="factorModel"
      title="Factor exposures"
      subtitle="Betas on Fama–French factors, with 95% intervals"
      actions={
        <Segmented
          label="Factor model"
          value={props.model}
          options={FACTOR_MODELS}
          onChange={props.onModel}
        />
      }
      loading={props.loading}
      refreshing={props.refreshing}
      error={props.error ?? undefined}
      chart={fit && <FactorChart exposures={fit.exposures} />}
      table={fit && <FactorTable exposures={fit.exposures} />}
      footer={
        fit && (
          <div className="stats" style={{ marginTop: 14 }}>
            <Stat
              info="alpha"
              label="Alpha"
              value={signedPercent(fit.alpha.estimate, 1)}
              detail={`Annualized · t ${number(fit.alpha.t_stat)}`}
            />
            <Stat
              info="rSquared"
              label="R²"
              value={percent(fit.r_squared, 0)}
              detail="Explained by factors"
            />
            <Stat
              info="systematicRisk"
              label="Systematic risk"
              value={percent(fit.systematic_share, 0)}
              detail={`Idiosyncratic ${percent(fit.idiosyncratic_share, 0)}`}
            />
          </div>
        )
      }
    />
  )
}

function VolatilityFooter({
  estimate,
  fellBack,
}: {
  estimate: VolatilityEstimate
  fellBack: boolean
}) {
  const fit = estimate.fit
  return (
    <div className="stack" style={{ gap: 12, marginTop: 14 }}>
      <div className="stats">
        <Stat info="volatilityNow" label="Now" value={percent(fit.current_volatility)} />
        <Stat
          info="garchForecast"
          label="In 21 days"
          value={percent(fit.forecast.at(-1)?.volatility)}
          detail="Forecast"
        />
        <Stat
          info="realizedVolatility"
          label="Realized"
          value={percent(fit.realized_volatility)}
          detail={`Over ${estimate.period.toUpperCase()}`}
        />
        {fit.model === 'garch' && (
          <Stat
            info="longRunVolatility"
            label="Long run"
            value={percent(fit.long_run_volatility)}
            detail={
              fit.half_life ? `Half-life ${number(fit.half_life, 0)} days` : 'No mean reversion'
            }
          />
        )}
      </div>
      {fellBack && (
        <Notice>GARCH needs R, which this server does not have, so EWMA is shown.</Notice>
      )}
      {estimate.notice && <Notice tone="warning">{estimate.notice}</Notice>}
      {(fit.warnings ?? []).map((warning) => (
        <Notice key={warning} tone="warning">
          {warning}
        </Notice>
      ))}
    </div>
  )
}
