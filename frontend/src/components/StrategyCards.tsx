import type { EnabledStrategyEntry } from '../api/stockBacktest'

const SIGNAL_LABELS: Record<string, string> = {
  ema_cross: 'EMA Cross',
  vwap_cross: 'VWAP Cross',
  ema_vwap: 'EMA + VWAP',
  orb: 'ORB Breakout',
  orb_direction: 'ORB Direction',
  vwap_rsi: 'VWAP + RSI',
  vwap_reclaim: 'VWAP Reclaim',
  bb_squeeze: 'BB Squeeze',
  rsi_reversal: 'RSI Reversal',
  confluence: 'Confluence',
}

interface Props {
  strategies: EnabledStrategyEntry[]
  selectedTicker: string | null
  onSelect: (ticker: string) => void
}

export function StrategyCards({ strategies, selectedTicker, onSelect }: Props) {
  if (strategies.length === 0) return null

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
      {strategies.map((s) => {
        const p = s.params || {}
        const isSelected = selectedTicker === s.ticker
        return (
          <button
            key={`${s.ticker}_${s.signal_type}_${s.timeframe}`}
            onClick={() => onSelect(s.ticker)}
            className={`text-left rounded-lg p-3 transition-all ring-1 ${
              isSelected
                ? 'bg-blue-900/30 ring-blue-500/60 shadow-md shadow-blue-500/10'
                : 'bg-surface ring-subtle hover:ring-blue-500/30 hover:bg-elevated'
            }`}
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-bold text-heading">{s.ticker}</span>
              <span className="text-xs text-tertiary font-medium">{s.timeframe}</span>
            </div>
            <p className="text-xs text-tertiary mb-2">
              {SIGNAL_LABELS[s.signal_type] || s.signal_type}
            </p>
            <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-xs">
              <span className="text-secondary">SL</span>
              <span className="text-red-400 text-right">{p.stop_loss_percent}%</span>
              <span className="text-secondary">PT</span>
              <span className="text-green-400 text-right">{p.profit_target_percent}%</span>
              <span className="text-secondary">Trail</span>
              <span className="text-orange-400 text-right">{p.trailing_stop_percent}%</span>
              <span className="text-secondary">Hold</span>
              <span className="text-blue-400 text-right">{p.max_hold_minutes}m</span>
            </div>
          </button>
        )
      })}
    </div>
  )
}
