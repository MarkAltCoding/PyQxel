import { useMemo } from 'react'
import type { EquityPoint } from '../api/types'
import { money, shortDate, signedPercent } from '../lib/format'
import { EChart } from './EChart'
import { axisStyle, escapeHtml, tooltipRow, tooltipStyle, useChartTheme } from './theme'

/** Growth of $10,000 for the strategy and buy-and-hold, on one axis, ends labeled. */
export function EquityChart({ points, start = 10_000 }: { points: EquityPoint[]; start?: number }) {
  const theme = useChartTheme()
  const option = useMemo(() => {
    const axis = axisStyle(theme)
    const dates = points.map((point) => point.timestamp.slice(0, 10))
    const endLabel = (name: string) => ({
      show: true,
      color: theme.textSecondary,
      fontSize: 11,
      formatter: (params: { value: number }) => `${name} ${money(params.value, 'USD', 0)}`,
    })
    return {
      animation: false,
      textStyle: { fontFamily: theme.font },
      tooltip: {
        trigger: 'axis',
        ...tooltipStyle(theme),
        formatter: (params: { dataIndex: number }[]) => {
          const point = points[params[0]?.dataIndex ?? -1]
          if (!point) return ''
          return (
            `<div>${escapeHtml(shortDate(point.timestamp.slice(0, 10)))}</div>` +
            tooltipRow(
              theme.series1,
              money(point.strategy * start, 'USD', 0),
              `Strategy (${signedPercent(point.strategy - 1)})`,
            ) +
            tooltipRow(
              theme.series2,
              money(point.benchmark * start, 'USD', 0),
              `Buy and hold (${signedPercent(point.benchmark - 1)})`,
            )
          )
        },
      },
      grid: { left: 8, right: 110, top: 12, bottom: 4, containLabel: true },
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
        scale: true,
        ...axis,
        axisLine: { show: false },
        axisLabel: { ...axis.axisLabel, formatter: (v: number) => money(v, 'USD', 0) },
      },
      dataZoom: [{ type: 'inside', zoomOnMouseWheel: 'ctrl' }],
      series: [
        {
          name: 'Strategy',
          type: 'line',
          data: points.map((point) => point.strategy * start),
          showSymbol: false,
          lineStyle: { width: 2, color: theme.series1 },
          itemStyle: { color: theme.series1 },
          endLabel: endLabel('Strategy'),
          z: 3,
        },
        {
          name: 'Buy and hold',
          type: 'line',
          data: points.map((point) => point.benchmark * start),
          showSymbol: false,
          lineStyle: { width: 2, color: theme.series2 },
          itemStyle: { color: theme.series2 },
          endLabel: endLabel('Hold'),
        },
      ],
    }
  }, [points, start, theme])
  return (
    <EChart option={option} height={320} label="Growth of $10,000: strategy and buy-and-hold" />
  )
}

export function EquityTable({ points, start = 10_000 }: { points: EquityPoint[]; start?: number }) {
  // Every bar would be thousands of rows; month-ends keep the table readable.
  const rows = points.filter((point, index) => {
    const next = points[index + 1]
    return !next || next.timestamp.slice(0, 7) !== point.timestamp.slice(0, 7)
  })
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Month end</th>
          <th className="num">Strategy</th>
          <th className="num">Buy and hold</th>
        </tr>
      </thead>
      <tbody>
        {rows.reverse().map((point) => (
          <tr key={point.timestamp}>
            <td>{shortDate(point.timestamp.slice(0, 10))}</td>
            <td className="num">{money(point.strategy * start, 'USD', 0)}</td>
            <td className="num">{money(point.benchmark * start, 'USD', 0)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
