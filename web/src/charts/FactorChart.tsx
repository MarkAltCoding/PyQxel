import { useMemo } from 'react'
import type { FactorRegression } from '../api/types'
import { number, percent } from '../lib/format'
import { EChart } from './EChart'
import { axisStyle, escapeHtml, tooltipStyle, useChartTheme } from './theme'

import { FACTOR_NAMES, FACTOR_SHORT_NAMES } from '../lib/factors'
import { InfoTip } from '../components/InfoTip'

type Exposure = FactorRegression['fit']['exposures'][number]

const Z_95 = 1.96

/**
 * Factor betas as bars from zero with 95% confidence whiskers. One series, one color;
 * the sign is read from the zero line, not from color.
 */
export function FactorChart({ exposures }: { exposures: Exposure[] }) {
  const theme = useChartTheme()
  const option = useMemo(() => {
    const axis = axisStyle(theme)
    const rows = [...exposures].reverse()
    // Each beta is printed in its row label, clear of the bar and its whiskers.
    const names = rows.map(
      (row) => `${FACTOR_SHORT_NAMES[row.factor]}  {value|${number(row.estimate)}}`,
    )
    return {
      animation: false,
      textStyle: { fontFamily: theme.font },
      tooltip: {
        trigger: 'item',
        ...tooltipStyle(theme),
        formatter: (params: { dataIndex: number }) => {
          const row = rows[params.dataIndex]
          if (!row) return ''
          const low = row.estimate - Z_95 * row.std_error
          const high = row.estimate + Z_95 * row.std_error
          return (
            `<div><strong>${escapeHtml(FACTOR_NAMES[row.factor])}</strong></div>` +
            `<div>Beta <strong>${number(row.estimate)}</strong></div>` +
            `<div>95% interval ${number(low)} to ${number(high)}</div>` +
            `<div>t ${number(row.t_stat)} · ${percent(row.variance_share)} of variance</div>`
          )
        },
      },
      grid: { left: 8, right: 24, top: 8, bottom: 4, containLabel: true },
      xAxis: {
        type: 'value',
        ...axis,
        axisLine: { show: false },
        splitNumber: 4,
        axisLabel: {
          ...axis.axisLabel,
          hideOverlap: true,
          formatter: (v: number) => number(v, 1),
        },
      },
      yAxis: {
        type: 'category',
        data: names,
        ...axis,
        axisLabel: {
          ...axis.axisLabel,
          color: theme.textSecondary,
          fontSize: 12,
          rich: { value: { color: theme.textPrimary, fontWeight: 600, fontSize: 12 } },
        },
        splitLine: { show: false },
      },
      series: [
        {
          type: 'bar',
          data: rows.map((row) => row.estimate),
          barWidth: 14,
          itemStyle: { color: theme.series1, borderRadius: 4 },
          emphasis: { itemStyle: { opacity: 0.85 } },
        },
        {
          type: 'custom',
          silent: true,
          data: rows.map((row, index) => [
            index,
            row.estimate - Z_95 * row.std_error,
            row.estimate + Z_95 * row.std_error,
          ]),
          renderItem: (
            _params: unknown,
            api: {
              value: (dimension: number) => number
              coord: (point: [number, number]) => [number, number]
            },
          ) => {
            const category = api.value(0)
            const [x1, y] = api.coord([api.value(1), category])
            const [x2] = api.coord([api.value(2), category])
            const style = { stroke: theme.textSecondary, lineWidth: 1.5 }
            return {
              type: 'group',
              children: [
                { type: 'line', shape: { x1, y1: y, x2, y2: y }, style },
                { type: 'line', shape: { x1, y1: y - 5, x2: x1, y2: y + 5 }, style },
                { type: 'line', shape: { x1: x2, y1: y - 5, x2, y2: y + 5 }, style },
              ],
            }
          },
          encode: { x: [1, 2], y: 0 },
          z: 3,
        },
      ],
    }
  }, [exposures, theme])
  return (
    <EChart
      option={option}
      height={Math.max(160, exposures.length * 44 + 30)}
      label="Factor betas with 95% confidence intervals"
    />
  )
}

export function FactorTable({ exposures }: { exposures: Exposure[] }) {
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Factor</th>
          <th className="num">
            Beta
            <InfoTip topic="factorBeta" />
          </th>
          <th className="num">
            Std. error
            <InfoTip topic="standardError" />
          </th>
          <th className="num">
            t
            <InfoTip topic="tStat" />
          </th>
          <th className="num">
            p
            <InfoTip topic="pValue" />
          </th>
          <th className="num">
            Share of variance
            <InfoTip topic="shareOfVariance" />
          </th>
        </tr>
      </thead>
      <tbody>
        {exposures.map((row) => (
          <tr key={row.factor}>
            <td>{FACTOR_NAMES[row.factor]}</td>
            <td className="num">{number(row.estimate)}</td>
            <td className="num">{number(row.std_error)}</td>
            <td className="num">{number(row.t_stat)}</td>
            <td className="num">{number(row.p_value, 3)}</td>
            <td className="num">{percent(row.variance_share)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
