import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, CustomChart, HeatmapChart, LineChart } from 'echarts/charts'
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'

echarts.use([
  BarChart,
  CustomChart,
  HeatmapChart,
  LineChart,
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
  SVGRenderer,
])

export type ChartOption = echarts.EChartsCoreOption

interface Props {
  option: ChartOption
  height: number
  /** Describes the chart for screen readers; the table view carries the values. */
  label: string
}

/** Renders an ECharts option, resizing with its container. */
export function EChart({ option, height, label }: Props) {
  const element = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!element.current) return
    const instance = echarts.init(element.current, null, { renderer: 'svg' })
    chart.current = instance
    const observer = new ResizeObserver(() => instance.resize())
    observer.observe(element.current)
    return () => {
      observer.disconnect()
      instance.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true })
  }, [option])

  return <div ref={element} className="chart" style={{ height }} role="img" aria-label={label} />
}
