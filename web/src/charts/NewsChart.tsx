import { useMemo } from 'react'
import type { NewsDay } from '../api/types'
import { shortDate } from '../lib/format'
import { EChart } from './EChart'
import { axisStyle, escapeHtml, tooltipStyle, useChartTheme } from './theme'

const plural = (count: number) => `${count} ${count === 1 ? 'article' : 'articles'}`

/** Articles per day as thin bars, one series; a spike in coverage often marks an event. */
export function NewsChart({ daily }: { daily: NewsDay[] }) {
  const theme = useChartTheme()
  const option = useMemo(() => {
    const axis = axisStyle(theme)
    return {
      animation: false,
      textStyle: { fontFamily: theme.font },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: theme.grid } },
        ...tooltipStyle(theme),
        formatter: (params: { dataIndex: number }[]) => {
          const day = daily[params[0]?.dataIndex ?? -1]
          if (!day) return ''
          return (
            `<div>${escapeHtml(shortDate(day.day))}</div>` +
            `<div><strong>${plural(day.articles)}</strong></div>`
          )
        },
      },
      grid: { left: 4, right: 8, top: 8, bottom: 4, containLabel: true },
      xAxis: {
        type: 'category',
        data: daily.map((day) => day.day),
        ...axis,
        axisLabel: {
          ...axis.axisLabel,
          hideOverlap: true,
          formatter: (value: string) => shortDate(value).replace(/, \d{4}$/, ''),
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        splitNumber: 3,
        ...axis,
        axisLine: { show: false },
      },
      series: [
        {
          type: 'bar',
          data: daily.map((day) => day.articles),
          barMaxWidth: 16,
          itemStyle: { color: theme.series1, borderRadius: [4, 4, 0, 0] },
          emphasis: { itemStyle: { opacity: 0.85 } },
        },
      ],
    }
  }, [daily, theme])
  return <EChart option={option} height={140} label="Articles per day over the last two weeks" />
}

export function NewsTable({ daily }: { daily: NewsDay[] }) {
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Day</th>
          <th className="num">Articles</th>
        </tr>
      </thead>
      <tbody>
        {[...daily].reverse().map((day) => (
          <tr key={day.day}>
            <td>{shortDate(day.day)}</td>
            <td className="num">{day.articles}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
