import { useMemo } from 'react'
import type { OHLCVBar } from '../api/types'
import { compact, money, shortDate } from '../lib/format'
import { EChart } from './EChart'
import { axisStyle, escapeHtml, tooltipRow, tooltipStyle, useChartTheme } from './theme'

const day = (timestamp: string) => timestamp.slice(0, 10)

/**
 * Closing prices over volume: two plots sharing the date axis and one crosshair, each
 * with its own y scale (stacked, never a dual axis).
 */
export function PriceChart({ bars, currency }: { bars: OHLCVBar[]; currency: string }) {
  const theme = useChartTheme()
  const option = useMemo(() => {
    const axis = axisStyle(theme)
    const dates = bars.map((bar) => day(bar.timestamp))
    const hasVolume = bars.some((bar) => bar.volume)
    return {
      animation: false,
      textStyle: { fontFamily: theme.font },
      axisPointer: { link: [{ xAxisIndex: 'all' }], lineStyle: { color: theme.axis } },
      tooltip: {
        trigger: 'axis',
        ...tooltipStyle(theme),
        axisPointer: { type: 'line' },
        formatter: (params: { dataIndex: number }[]) => {
          const bar = bars[params[0]?.dataIndex ?? -1]
          if (!bar) return ''
          return (
            `<div>${escapeHtml(shortDate(day(bar.timestamp)))}</div>` +
            tooltipRow(theme.series1, money(bar.close, currency), 'Close') +
            (bar.volume ? tooltipRow(theme.seq200, compact(bar.volume, ''), 'Volume') : '')
          )
        },
      },
      grid: hasVolume
        ? [
            { left: 8, right: 40, top: 8, height: '66%', containLabel: true },
            { left: 8, right: 40, top: '78%', bottom: 4, containLabel: true },
          ]
        : [{ left: 8, right: 40, top: 8, bottom: 4, containLabel: true }],
      xAxis: [
        {
          type: 'category',
          data: dates,
          boundaryGap: false,
          ...axis,
          axisLabel: { ...axis.axisLabel, show: !hasVolume, formatter: shortDate },
          splitLine: { show: false },
        },
        ...(hasVolume
          ? [
              {
                type: 'category',
                gridIndex: 1,
                data: dates,
                ...axis,
                axisLabel: { ...axis.axisLabel, formatter: shortDate },
                splitLine: { show: false },
              },
            ]
          : []),
      ],
      yAxis: [
        {
          type: 'value',
          scale: true,
          ...axis,
          axisLine: { show: false },
          axisLabel: { ...axis.axisLabel, formatter: (v: number) => money(v, currency, 0) },
        },
        ...(hasVolume
          ? [
              {
                type: 'value',
                gridIndex: 1,
                ...axis,
                axisLine: { show: false },
                splitNumber: 2,
                axisLabel: { ...axis.axisLabel, formatter: (v: number) => compact(v, '') },
              },
            ]
          : []),
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: hasVolume ? [0, 1] : [0], zoomOnMouseWheel: 'ctrl' },
      ],
      series: [
        {
          name: 'Close',
          type: 'line',
          data: bars.map((bar) => bar.close),
          showSymbol: false,
          lineStyle: { width: 2, color: theme.series1 },
          itemStyle: { color: theme.series1 },
        },
        ...(hasVolume
          ? [
              {
                name: 'Volume',
                type: 'bar',
                xAxisIndex: 1,
                yAxisIndex: 1,
                data: bars.map((bar) => bar.volume ?? 0),
                itemStyle: { color: theme.seq200 },
                barCategoryGap: '20%',
              },
            ]
          : []),
      ],
    }
  }, [bars, currency, theme])
  return <EChart option={option} height={340} label="Closing price and volume over time" />
}

export function PriceTable({ bars, currency }: { bars: OHLCVBar[]; currency: string }) {
  const rows = [...bars].reverse()
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Date</th>
          <th className="num">Open</th>
          <th className="num">High</th>
          <th className="num">Low</th>
          <th className="num">Close</th>
          <th className="num">Volume</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((bar) => (
          <tr key={bar.timestamp}>
            <td>{shortDate(day(bar.timestamp))}</td>
            <td className="num">{money(bar.open, currency)}</td>
            <td className="num">{money(bar.high, currency)}</td>
            <td className="num">{money(bar.low, currency)}</td>
            <td className="num">{money(bar.close, currency)}</td>
            <td className="num">{compact(bar.volume, '')}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
