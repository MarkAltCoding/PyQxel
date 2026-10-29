import { useMemo } from 'react'
import type { SimulationResponse } from '../api/types'
import { money } from '../lib/format'
import { EChart } from './EChart'
import { axisStyle, tooltipRow, tooltipStyle, useChartTheme } from './theme'
import { InfoTip } from '../components/InfoTip'

type FanPoint = SimulationResponse['simulation']['fan_chart'][number]

/**
 * Percentile bands of simulated portfolio value: the 90% range lightest, the middle 50%
 * darker, the median as a line. One hue, light to dark with likelihood.
 */
export function FanChart({ fan, initial }: { fan: FanPoint[]; initial: number }) {
  const theme = useChartTheme()
  const option = useMemo(() => {
    const axis = axisStyle(theme)
    const band = (name: string, values: number[], color: string) => ({
      name,
      type: 'line',
      stack: 'fan',
      data: values,
      showSymbol: false,
      silent: true,
      lineStyle: { opacity: 0 },
      areaStyle: { color, opacity: 1 },
    })
    return {
      animation: false,
      textStyle: { fontFamily: theme.font },
      tooltip: {
        trigger: 'axis',
        ...tooltipStyle(theme),
        formatter: (params: { dataIndex: number }[]) => {
          const point = fan[params[0]?.dataIndex ?? -1]
          if (!point) return ''
          return (
            `<div>Day ${point.day}</div>` +
            tooltipRow(theme.seq300, money(point.p95, 'USD', 0), '95th percentile') +
            tooltipRow(theme.seq450, money(point.p75, 'USD', 0), '75th percentile') +
            tooltipRow(theme.seq600, money(point.p50, 'USD', 0), 'Median') +
            tooltipRow(theme.seq450, money(point.p25, 'USD', 0), '25th percentile') +
            tooltipRow(theme.seq300, money(point.p05, 'USD', 0), '5th percentile')
          )
        },
      },
      grid: { left: 8, right: 16, top: 12, bottom: 24, containLabel: true },
      xAxis: {
        type: 'category',
        data: fan.map((point) => point.day),
        boundaryGap: false,
        name: 'Trading days ahead',
        nameLocation: 'middle',
        nameGap: 26,
        nameTextStyle: { color: theme.textMuted, fontSize: 11 },
        ...axis,
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        ...axis,
        axisLine: { show: false },
        axisLabel: { ...axis.axisLabel, formatter: (v: number) => money(v, 'USD', 0) },
      },
      series: [
        {
          ...band(
            'base',
            fan.map((p) => p.p05),
            'transparent',
          ),
          areaStyle: { opacity: 0 },
        },
        band(
          '5th–25th',
          fan.map((p) => p.p25 - p.p05),
          theme.seq100,
        ),
        band(
          '25th–50th',
          fan.map((p) => p.p50 - p.p25),
          theme.seq200,
        ),
        band(
          '50th–75th',
          fan.map((p) => p.p75 - p.p50),
          theme.seq200,
        ),
        band(
          '75th–95th',
          fan.map((p) => p.p95 - p.p75),
          theme.seq100,
        ),
        {
          name: 'Median',
          type: 'line',
          data: fan.map((p) => p.p50),
          showSymbol: false,
          lineStyle: { width: 2, color: theme.seq600 },
          itemStyle: { color: theme.seq600 },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: theme.textMuted, width: 1, type: 'solid' },
            label: {
              color: theme.textSecondary,
              formatter: `Start ${money(initial, 'USD', 0)}`,
              position: 'insideStartTop',
            },
            data: [{ yAxis: initial }],
          },
        },
      ],
    }
  }, [fan, initial, theme])
  return (
    <EChart option={option} height={320} label="Simulated portfolio value percentiles by day" />
  )
}

export function FanTable({ fan }: { fan: FanPoint[] }) {
  return (
    <table className="data">
      <thead>
        <tr>
          <th className="num">Day</th>
          <th className="num">5th</th>
          <th className="num">25th</th>
          <th className="num">
            Median
            <InfoTip topic="simulationPercentiles" />
          </th>
          <th className="num">75th</th>
          <th className="num">95th</th>
        </tr>
      </thead>
      <tbody>
        {fan.map((point) => (
          <tr key={point.day}>
            <td className="num">{point.day}</td>
            <td className="num">{money(point.p05, 'USD', 0)}</td>
            <td className="num">{money(point.p25, 'USD', 0)}</td>
            <td className="num">{money(point.p50, 'USD', 0)}</td>
            <td className="num">{money(point.p75, 'USD', 0)}</td>
            <td className="num">{money(point.p95, 'USD', 0)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
