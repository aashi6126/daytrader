import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts'
import type { PnLDataPoint, PnLSummaryData } from '../types'
import { formatCurrency, formatTime } from '../utils/format'
import { useChartColors } from '../hooks/useChartColors'

type PnLPeriod = 'daily' | 'weekly' | 'monthly'

interface Props {
  period: PnLPeriod
  data: PnLDataPoint[]
  totalPnl: number
  summaryData?: PnLSummaryData | null
}

function formatDateShort(dateStr: string) {
  const d = new Date(dateStr + 'T12:00:00')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function PnLChart({ period, data, totalPnl, summaryData }: Props) {
  const cc = useChartColors()

  if (period !== 'daily' && summaryData) {
    return <SummaryBarChart summaryData={summaryData} period={period} />
  }

  const isPositive = totalPnl >= 0
  const color = isPositive ? cc.green : cc.red

  if (data.length === 0) {
    return (
      <div className="bg-surface rounded-lg p-6">
        <h2 className="text-lg font-semibold mb-4">Cumulative P&L</h2>
        <div className="flex items-center justify-center h-64 text-muted">
          No closed trades yet today
        </div>
      </div>
    )
  }

  return (
    <div className="bg-surface rounded-lg p-6">
      <h2 className="text-lg font-semibold mb-1">Cumulative P&L</h2>
      <p className={`text-3xl font-bold mb-4 ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
        {formatCurrency(totalPnl)}
      </p>
      <ResponsiveContainer width="100%" height={300}>
        <AreaChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke={cc.grid} />
          <XAxis
            dataKey="timestamp"
            tickFormatter={formatTime}
            stroke={cc.axis}
            tick={{ fontSize: 12 }}
          />
          <YAxis
            tickFormatter={(v: number) => formatCurrency(v)}
            stroke={cc.axis}
            tick={{ fontSize: 12 }}
          />
          <Tooltip
            formatter={(value: number) => [formatCurrency(value), 'P&L']}
            labelFormatter={formatTime}
            contentStyle={{ backgroundColor: cc.tooltipBg, border: `1px solid ${cc.tooltipBorder}` }}
          />
          <ReferenceLine y={0} stroke={cc.ref} strokeDasharray="3 3" />
          <Area
            type="monotone"
            dataKey="cumulative_pnl"
            stroke={color}
            fill={isPositive ? '#22c55e20' : '#ef444420'}
            strokeWidth={2}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

function SummaryBarChart({ summaryData, period }: { summaryData: PnLSummaryData; period: PnLPeriod }) {
  const cc = useChartColors()
  const isPositive = summaryData.total_pnl >= 0
  const label = period === 'weekly' ? 'Weekly' : 'Monthly'

  if (summaryData.days.length === 0) {
    return (
      <div className="bg-surface rounded-lg p-6">
        <h2 className="text-lg font-semibold mb-4">{label} P&L</h2>
        <div className="flex items-center justify-center h-64 text-muted">
          No trades this {period === 'weekly' ? 'week' : 'month'}
        </div>
      </div>
    )
  }

  return (
    <div className="bg-surface rounded-lg p-6">
      <div className="flex items-baseline justify-between mb-1">
        <h2 className="text-lg font-semibold">{label} P&L</h2>
        <div className="flex items-center gap-4 text-xs text-secondary">
          <span>{summaryData.total_trades} trades</span>
          <span>{summaryData.win_rate.toFixed(0)}% win rate</span>
          <span>{summaryData.winning_trades}W / {summaryData.losing_trades}L</span>
        </div>
      </div>
      <p className={`text-3xl font-bold mb-4 ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
        {formatCurrency(summaryData.total_pnl)}
      </p>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={summaryData.days}>
          <CartesianGrid strokeDasharray="3 3" stroke={cc.grid} />
          <XAxis
            dataKey="trade_date"
            tickFormatter={formatDateShort}
            stroke={cc.axis}
            tick={{ fontSize: 12 }}
          />
          <YAxis
            tickFormatter={(v: number) => formatCurrency(v)}
            stroke={cc.axis}
            tick={{ fontSize: 12 }}
          />
          <Tooltip
            formatter={(value: number) => [formatCurrency(value), 'P&L']}
            labelFormatter={formatDateShort}
            contentStyle={{ backgroundColor: cc.tooltipBg, border: `1px solid ${cc.tooltipBorder}` }}
          />
          <ReferenceLine y={0} stroke={cc.ref} strokeDasharray="3 3" />
          <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
            {summaryData.days.map((day, idx) => (
              <Cell key={idx} fill={day.pnl >= 0 ? cc.green : cc.red} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
