import { useEffect, useState } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts'
import type { PriceSnapshot } from '../types'
import { fetchTradePrices } from '../api/trades'
import { formatCurrency } from '../utils/format'
import { useChartColors } from '../hooks/useChartColors'

interface Props {
  tradeId: number
}

function formatTime(ts: string): string {
  const d = new Date(ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z')
  return d.toLocaleString('en-US', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

export function TradePriceChart({ tradeId }: Props) {
  const cc = useChartColors()
  const [snapshots, setSnapshots] = useState<PriceSnapshot[]>([])
  const [entryPrice, setEntryPrice] = useState<number | null>(null)
  const [stopLoss, setStopLoss] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchTradePrices(tradeId)
      .then((res) => {
        if (!cancelled) {
          setSnapshots(res.snapshots)
          setEntryPrice(res.entry_price)
          setStopLoss(res.stop_loss_price)
        }
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [tradeId])

  if (loading) {
    return <div className="text-muted text-xs py-2">Loading price data...</div>
  }

  if (snapshots.length === 0) {
    return <div className="text-muted text-xs py-2">No price data recorded</div>
  }

  const chartData = snapshots.map((s) => ({
    time: formatTime(s.timestamp),
    price: s.price,
    max: s.highest_price_seen,
  }))

  const allPrices = snapshots.map((s) => s.price)
  if (entryPrice != null) allPrices.push(entryPrice)
  if (stopLoss != null) allPrices.push(stopLoss)
  const minPrice = Math.min(...allPrices) * 0.98
  const maxPrice = Math.max(...allPrices) * 1.02

  return (
    <div className="mt-3">
      <p className="text-[11px] uppercase tracking-wide text-muted mb-2">Price History</p>
      <div style={{ width: '100%', height: 180 }}>
        <ResponsiveContainer>
          <LineChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={cc.grid} />
            <XAxis
              dataKey="time"
              tick={{ fontSize: 10, fill: cc.axis }}
              interval="preserveStartEnd"
            />
            <YAxis
              domain={[minPrice, maxPrice]}
              tick={{ fontSize: 10, fill: cc.axis }}
              tickFormatter={(v: number) => `$${v.toFixed(2)}`}
              width={55}
            />
            <Tooltip
              contentStyle={{ backgroundColor: cc.tooltipBg, border: `1px solid ${cc.tooltipBorder}`, borderRadius: 6, fontSize: 12 }}
              labelStyle={{ color: cc.label }}
              formatter={(value: number, name: string) => [formatCurrency(value), name === 'price' ? 'Price' : 'Max Seen']}
            />
            {entryPrice != null && (
              <ReferenceLine y={entryPrice} stroke={cc.blue} strokeDasharray="4 4" label={{ value: 'Entry', position: 'right', fontSize: 10, fill: cc.blue }} />
            )}
            {stopLoss != null && (
              <ReferenceLine y={stopLoss} stroke={cc.lightRed} strokeDasharray="4 4" label={{ value: 'Stop', position: 'right', fontSize: 10, fill: cc.lightRed }} />
            )}
            <Line type="monotone" dataKey="price" stroke={cc.sky} strokeWidth={1.5} dot={false} />
            <Line type="monotone" dataKey="max" stroke={cc.amber} strokeWidth={1} dot={false} strokeDasharray="3 3" />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
