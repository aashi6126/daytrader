import type { DailyStats as DailyStatsType } from '../types'
import { formatCurrency } from '../utils/format'

interface Props {
  stats: DailyStatsType | null
}

export function DailyStats({ stats }: Props) {
  if (!stats) {
    return <div className="bg-surface rounded-lg p-4 animate-pulse h-20" />
  }

  const pnlPositive = stats.total_pnl >= 0

  return (
    <div className="bg-surface rounded-lg px-6 py-4 flex items-center gap-8">
      {/* Hero P&L */}
      <div className="flex items-baseline gap-3">
        <span className={`text-3xl font-bold ${pnlPositive ? 'text-green-400' : 'text-red-400'}`}>
          {formatCurrency(stats.total_pnl)}
        </span>
        <span className="text-sm text-secondary">today</span>
      </div>

      {/* Divider */}
      <div className="w-px h-10 bg-subtle" />

      {/* Secondary stats */}
      <div className="flex items-center gap-6 text-sm">
        <div className="flex items-center gap-1.5">
          <span className="text-green-400 font-semibold">{stats.winning_trades}W</span>
          <span className="text-muted">/</span>
          <span className="text-red-400 font-semibold">{stats.losing_trades}L</span>
        </div>
        <div>
          <span className={`font-semibold ${stats.win_rate >= 50 ? 'text-green-400' : 'text-yellow-400'}`}>
            {stats.win_rate.toFixed(0)}%
          </span>
          <span className="text-muted ml-1">win rate</span>
        </div>
        <div>
          <span className={`font-semibold ${stats.trades_remaining > 3 ? 'text-blue-400' : 'text-orange-400'}`}>
            {stats.trades_remaining}
          </span>
          <span className="text-muted ml-1">remaining</span>
        </div>
        {stats.open_positions > 0 && (
          <div>
            <span className="font-semibold text-yellow-400">{stats.open_positions}</span>
            <span className="text-muted ml-1">open</span>
          </div>
        )}
      </div>
    </div>
  )
}
