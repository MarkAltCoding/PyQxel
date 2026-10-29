import { useMemo } from 'react'
import type { CopulaFitResponse } from '../api/types'
import { number, percent } from '../lib/format'
import { EChart } from './EChart'
import { escapeHtml, tooltipStyle, useChartTheme } from './theme'
import { InfoTip } from '../components/InfoTip'

/** Largest matrix whose cells are labeled; beyond it values are in the tooltip and table. */
const LABELED_UP_TO = 8

/**
 * Kendall's tau between each pair: diverging blue (moving opposite) through gray
 * (independent) to red (moving together). The diagonal is left empty.
 */
export function CopulaHeatmap({ fit }: { fit: CopulaFitResponse }) {
  const theme = useChartTheme()
  const option = useMemo(() => {
    const symbols = fit.symbols
    const n = symbols.length
    const tails = new Map(
      fit.pairs.map((pair) => [pair.symbols.join('|'), pair.tail_dependence] as const),
    )
    const cells: [number, number, number][] = []
    for (let row = 0; row < n; row++) {
      for (let column = 0; column < n; column++) {
        const tau = fit.kendall_tau[row]?.[column]
        if (row !== column && tau !== undefined) cells.push([column, n - 1 - row, tau])
      }
    }
    const axisLabel = { color: theme.textSecondary, fontFamily: theme.font, fontSize: 11 }
    return {
      animation: false,
      textStyle: { fontFamily: theme.font },
      tooltip: {
        trigger: 'item',
        ...tooltipStyle(theme),
        formatter: (params: { value: [number, number, number] }) => {
          const [column, flipped, tau] = params.value
          const a = symbols[n - 1 - flipped] ?? ''
          const b = symbols[column] ?? ''
          const tail = tails.get(`${a}|${b}`) ?? tails.get(`${b}|${a}`)
          return (
            `<div>${escapeHtml(a)} and ${escapeHtml(b)}</div>` +
            `<div>Kendall's tau <strong>${number(tau)}</strong></div>` +
            (tail !== undefined
              ? `<div>Crash co-movement (tail dependence) ${percent(tail)}</div>`
              : '')
          )
        },
      },
      grid: { left: 8, right: 8, top: 8, bottom: 56, containLabel: true },
      xAxis: {
        type: 'category',
        data: symbols,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { ...axisLabel, interval: 0, rotate: n > 10 ? 45 : 0 },
        splitArea: { show: false },
      },
      yAxis: {
        type: 'category',
        data: [...symbols].reverse(),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { ...axisLabel, interval: 0 },
      },
      visualMap: {
        type: 'continuous',
        min: -1,
        max: 1,
        calculable: false,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        itemWidth: 12,
        itemHeight: 160,
        text: ['Move together  +1', '−1  Move opposite'],
        textStyle: { color: theme.textSecondary, fontSize: 11 },
        inRange: { color: [theme.divNeg, theme.divMid, theme.divPos] },
      },
      series: [
        {
          type: 'heatmap',
          data: cells,
          itemStyle: { borderColor: theme.surface, borderWidth: 2, borderRadius: 3 },
          emphasis: { itemStyle: { borderColor: theme.textPrimary, borderWidth: 2 } },
          label: {
            show: n <= LABELED_UP_TO,
            color: theme.textPrimary,
            fontSize: 11,
            formatter: (params: { value: [number, number, number] }) => number(params.value[2]),
          },
        },
      ],
    }
  }, [fit, theme])
  const size = Math.min(560, Math.max(260, fit.symbols.length * 52 + 100))
  return (
    <EChart option={option} height={size} label="Kendall's tau between each pair of holdings" />
  )
}

export function CopulaTable({ fit }: { fit: CopulaFitResponse }) {
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Pair</th>
          <th className="num">
            Kendall's tau
            <InfoTip topic="kendallTau" />
          </th>
          <th className="num">
            Model tail dependence
            <InfoTip topic="tailDependence" />
          </th>
          <th className="num">
            Observed lower tail
            <InfoTip topic="tailDependence" />
          </th>
          <th className="num">
            Observed upper tail
            <InfoTip topic="tailDependence" />
          </th>
        </tr>
      </thead>
      <tbody>
        {fit.pairs.map((pair) => (
          <tr key={pair.symbols.join('|')}>
            <td>{pair.symbols.join(' · ')}</td>
            <td className="num">{number(pair.kendall_tau)}</td>
            <td className="num">{percent(pair.tail_dependence)}</td>
            <td className="num">{percent(pair.empirical_lower_tail)}</td>
            <td className="num">{percent(pair.empirical_upper_tail)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
