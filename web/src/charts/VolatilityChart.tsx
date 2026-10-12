import { useMemo } from 'react'
import type { VolatilityEstimate } from '../api/types'
import { percent, shortDate } from '../lib/format'
import { EChart } from './EChart'
import { axisStyle, escapeHtml, tooltipRow, tooltipStyle, useChartTheme } from './theme'

type Fit = VolatilityEstimate['fit']

/** Labels forecast steps as the business days after the last close. */
function forecastDates(last: string, steps: number): string[] {
  const dates: string[] = []
  const date = new Date(`${last}T00:00:00Z`)
  while (dates.length < steps) {
    date.setUTCDate(date.getUTCDate() + 1)
    const weekday = date.getUTCDay()
    if (weekday !== 0 && weekday !== 6) dates.push(date.toISOString().slice(0, 10))
  }
  return dates
}

/**
 * In-sample conditional volatility from `since` on (the fit may cover a longer window),
 * then the forecast continuing it, dashed as a projection.
 */
export function VolatilityChart({ fit, since }: { fit: Fit; since?: string }) {
  const theme = useChartTheme()
  const option = useMemo(() => {
    const axis = axisStyle(theme)
    const history = since
      ? fit.conditional_volatility.filter((point) => point.timestamp.slice(0, 10) >= since)
      : fit.conditional_volatility
    const last = history.at(-1)?.timestamp.slice(0, 10) ?? ''
    const future = forecastDates(last, fit.forecast.length)
    const dates = [...history.map((point) => point.timestamp.slice(0, 10)), ...future]
    const past = history.map((point) => point.volatility)
    // The forecast line starts at the last fitted value so the two lines join.
    const ahead = [
      ...past.slice(0, -1).map(() => null),
      past.at(-1) ?? null,
      ...fit.forecast.map((step) => step.volatility),
    ]
    const longRun = fit.model === 'garch' ? fit.long_run_volatility : null
    return {
      animation: false,
      textStyle: { fontFamily: theme.font },
      tooltip: {
        trigger: 'axis',
        ...tooltipStyle(theme),
        formatter: (params: { dataIndex: number }[]) => {
          const index = params[0]?.dataIndex ?? -1
          const isForecast = index >= past.length
          const value = isForecast ? ahead[index] : past[index]
          return (
            `<div>${escapeHtml(shortDate(dates[index]))}</div>` +
            tooltipRow(
              theme.series1,
              percent(value ?? null),
              isForecast ? `Forecast, ${index - past.length + 1} ahead` : 'Conditional volatility',
              isForecast,
            )
          )
        },
      },
      grid: { left: 8, right: 40, top: 12, bottom: 4, containLabel: true },
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: false,
        ...axis,
        axisLabel: { ...axis.axisLabel, formatter: shortDate },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        min: 0,
        ...axis,
        axisLine: { show: false },
        axisLabel: { ...axis.axisLabel, formatter: (v: number) => percent(v, 0) },
      },
      dataZoom: [{ type: 'inside', zoomOnMouseWheel: 'ctrl' }],
      series: [
        {
          name: 'Conditional volatility',
          type: 'line',
          data: past,
          showSymbol: false,
          lineStyle: { width: 2, color: theme.series1 },
          itemStyle: { color: theme.series1 },
          markLine: longRun
            ? {
                silent: true,
                symbol: 'none',
                lineStyle: { color: theme.textMuted, width: 1, type: 'solid' },
                label: {
                  color: theme.textSecondary,
                  formatter: `Long run ${percent(longRun)}`,
                  position: 'insideEndTop',
                },
                data: [{ yAxis: longRun }],
              }
            : undefined,
        },
        {
          name: 'Forecast',
          type: 'line',
          data: ahead,
          showSymbol: false,
          lineStyle: { width: 2, color: theme.series1, type: 'dashed' },
          itemStyle: { color: theme.series1 },
        },
      ],
    }
  }, [fit, since, theme])
  return (
    <EChart option={option} height={280} label="Annualized conditional volatility and forecast" />
  )
}

export function VolatilityTable({ fit }: { fit: Fit }) {
  const recent = fit.conditional_volatility.slice(-30).reverse()
  return (
    <table className="data">
      <thead>
        <tr>
          <th>When</th>
          <th className="num">Annualized volatility</th>
        </tr>
      </thead>
      <tbody>
        {fit.forecast.map((step) => (
          <tr key={`f${step.step}`}>
            <td>{step.step} ahead (forecast)</td>
            <td className="num">{percent(step.volatility)}</td>
          </tr>
        ))}
        {recent.map((point) => (
          <tr key={point.timestamp}>
            <td>{shortDate(point.timestamp.slice(0, 10))}</td>
            <td className="num">{percent(point.volatility)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
