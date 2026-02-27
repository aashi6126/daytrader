import { useEffect, useRef } from 'react'
import { createChart, createSeriesMarkers, CandlestickSeries, HistogramSeries, type IChartApi, type ISeriesApi, type CandlestickData, type HistogramData, type Time, type SeriesMarker } from 'lightweight-charts'
import type { CandleData, ChartMarker, PivotLevels } from '../api/dashboard'
import { getChartColors } from '../utils/chartColors'

interface Props {
  data: CandleData[]
  markers?: ChartMarker[]
  title?: string
  pivots?: PivotLevels | null
}

export function CandlestickChart({ data, markers = [], title = 'SPY 5m', pivots }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const markersRef = useRef<ReturnType<typeof createSeriesMarkers> | null>(null)
  const pivotLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([])

  useEffect(() => {
    if (!containerRef.current) return

    const colors = getChartColors()

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: 'transparent' },
        textColor: colors.axis,
      },
      grid: {
        vertLines: { color: colors.grid },
        horzLines: { color: colors.grid },
      },
      crosshair: {
        mode: 0,
      },
      rightPriceScale: {
        borderColor: colors.grid,
      },
      timeScale: {
        borderColor: colors.grid,
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: false },
    })

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: colors.green,
      downColor: colors.red,
      borderUpColor: colors.green,
      borderDownColor: colors.red,
      wickUpColor: colors.green,
      wickDownColor: colors.red,
    })

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    })

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries
    volumeSeriesRef.current = volumeSeries

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width } = entry.contentRect
        chart.applyOptions({ width })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      volumeSeriesRef.current = null
      markersRef.current = null
      pivotLinesRef.current = []
    }
  }, [])

  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || data.length === 0) return

    const colors = getChartColors()

    const candleData: CandlestickData<Time>[] = data.map((d) => ({
      time: d.time as Time,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }))

    const volumeData: HistogramData<Time>[] = data.map((d) => ({
      time: d.time as Time,
      value: d.volume,
      color: d.close >= d.open ? colors.green + '30' : colors.red + '30',
    }))

    candleSeriesRef.current.setData(candleData)
    volumeSeriesRef.current.setData(volumeData)

    chartRef.current?.timeScale().fitContent()
  }, [data])

  // Pivot price lines
  useEffect(() => {
    if (!candleSeriesRef.current) return

    // Remove existing pivot lines
    for (const line of pivotLinesRef.current) {
      candleSeriesRef.current.removePriceLine(line)
    }
    pivotLinesRef.current = []

    if (!pivots) return

    const pivotConfig = [
      { price: pivots.r2, color: '#ef4444', title: 'R2', lineStyle: 2 },
      { price: pivots.r1, color: '#f87171', title: 'R1', lineStyle: 2 },
      { price: pivots.pivot, color: '#a78bfa', title: 'P', lineStyle: 0 },
      { price: pivots.s1, color: '#34d399', title: 'S1', lineStyle: 2 },
      { price: pivots.s2, color: '#22c55e', title: 'S2', lineStyle: 2 },
    ] as const

    for (const cfg of pivotConfig) {
      const line = candleSeriesRef.current.createPriceLine({
        price: cfg.price,
        color: cfg.color,
        lineWidth: cfg.title === 'P' ? 2 : 1,
        lineStyle: cfg.lineStyle,
        axisLabelVisible: true,
        title: cfg.title,
      })
      pivotLinesRef.current.push(line)
    }
  }, [pivots])

  // Update markers when data or markers change
  useEffect(() => {
    if (!candleSeriesRef.current || data.length === 0) return

    // Build time set from candle data for snapping markers to valid bar times
    const candleTimes = new Set(data.map((d) => d.time))
    const sortedTimes = data.map((d) => d.time).sort((a, b) => a - b)

    // Snap a marker time to the nearest candle bar time
    const snapToBar = (t: number): number => {
      if (candleTimes.has(t)) return t
      let closest = sortedTimes[0]
      for (const ct of sortedTimes) {
        if (Math.abs(ct - t) < Math.abs(closest - t)) closest = ct
      }
      return closest
    }

    const seriesMarkers: SeriesMarker<Time>[] = markers.map((m) => {
      const snappedTime = snapToBar(m.time)

      if (m.type === 'entry') {
        return {
          time: snappedTime as Time,
          position: m.direction === 'CALL' ? 'belowBar' : 'aboveBar',
          color: '#22c55e',
          shape: m.direction === 'CALL' ? 'arrowUp' : 'arrowDown',
          text: m.label,
        } as SeriesMarker<Time>
      }

      if (m.type === 'exit') {
        const isProfit = m.label.includes('+')
        return {
          time: snappedTime as Time,
          position: 'aboveBar',
          color: isProfit ? '#22c55e' : '#ef4444',
          shape: 'circle',
          text: m.label,
        } as SeriesMarker<Time>
      }

      // Signal marker
      return {
        time: snappedTime as Time,
        position: m.direction === 'CALL' ? 'belowBar' : 'aboveBar',
        color: m.direction === 'CALL' ? '#60a5fa' : '#f87171',
        shape: m.direction === 'CALL' ? 'arrowUp' : 'arrowDown',
        text: '',
      } as SeriesMarker<Time>
    })

    // Sort by time (required by lightweight-charts)
    seriesMarkers.sort((a, b) => (a.time as number) - (b.time as number))

    if (markersRef.current) {
      markersRef.current.setMarkers(seriesMarkers)
    } else {
      markersRef.current = createSeriesMarkers(candleSeriesRef.current, seriesMarkers)
    }
  }, [data, markers])

  return (
    <div className="bg-surface rounded-lg ring-1 ring-subtle p-4">
      <div className="flex items-center gap-4 mb-2">
        <h3 className="text-sm font-medium text-secondary">{title}</h3>
        <div className="flex items-center gap-3 text-xs text-muted">
          <span className="flex items-center gap-1">
            <span className="inline-block w-2 h-2 bg-blue-400 rounded-full" /> Signal
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-2 h-2 bg-green-400 rounded-full" /> Entry
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-2 h-2 bg-red-400 rounded-full" /> Exit
          </span>
          {pivots && (
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 bg-purple-400 rounded-full" /> Pivots
            </span>
          )}
        </div>
      </div>
      <div ref={containerRef} style={{ height: 300 }} />
    </div>
  )
}
