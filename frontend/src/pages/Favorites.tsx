import { useEffect, useMemo, useState } from 'react'
import {
  getFavorites, deleteFavorite,
  getStrategyStatus, enableStrategy, disableStrategy,
  type FavoriteStrategy, type StockBacktestParams,
  type EnabledStrategyEntry,
} from '../api/stockBacktest'
import { formatCurrency } from '../utils/format'

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

interface FavoritesProps {
  onLoadBacktest: (ticker: string, params: StockBacktestParams) => void
}

export default function Favorites({ onLoadBacktest }: FavoritesProps) {
  const [favorites, setFavorites] = useState<FavoriteStrategy[]>([])
  const [loading, setLoading] = useState(true)
  const [filterTicker, setFilterTicker] = useState<string | null>(null)
  const [enabledStrategies, setEnabledStrategies] = useState<EnabledStrategyEntry[]>([])
  const [togglingIds, setTogglingIds] = useState<Set<number>>(new Set())

  useEffect(() => {
    getFavorites()
      .then(setFavorites)
      .catch(() => {})
      .finally(() => setLoading(false))
    getStrategyStatus()
      .then((res) => setEnabledStrategies(res.strategies))
      .catch(() => {})
  }, [])

  const isEnabled = (fav: FavoriteStrategy): boolean => {
    const p = fav.params
    return enabledStrategies.some(
      (s) =>
        s.ticker === fav.ticker &&
        s.signal_type === ((p.signal_type as string) || 'ema_cross') &&
        s.timeframe === ((p.bar_interval as string) || '5m')
    )
  }

  const handleToggleEnable = async (fav: FavoriteStrategy) => {
    const p = fav.params
    const signalType = (p.signal_type as string) || 'ema_cross'
    const timeframe = (p.bar_interval as string) || '5m'

    setTogglingIds((prev) => new Set(prev).add(fav.id))
    try {
      let res
      if (isEnabled(fav)) {
        res = await disableStrategy({ ticker: fav.ticker, timeframe, signal_type: signalType })
      } else {
        res = await enableStrategy({
          ticker: fav.ticker,
          timeframe,
          signal_type: signalType,
          params: fav.params,
        })
      }
      setEnabledStrategies(res.strategies)
    } catch (err) {
      console.error('Failed to toggle strategy:', err)
    } finally {
      setTogglingIds((prev) => {
        const next = new Set(prev)
        next.delete(fav.id)
        return next
      })
    }
  }

  const tickers = useMemo(() => {
    const set = new Set(favorites.map((f) => f.ticker))
    return Array.from(set).sort()
  }, [favorites])

  const filtered = useMemo(() => {
    if (!filterTicker) return favorites
    return favorites.filter((f) => f.ticker === filterTicker)
  }, [favorites, filterTicker])

  const handleRemove = async (id: number) => {
    try {
      await deleteFavorite(id)
      setFavorites((prev) => prev.filter((f) => f.id !== id))
    } catch (err) {
      console.error('Failed to delete favorite:', err)
    }
  }

  const handleLoad = (fav: FavoriteStrategy) => {
    const p = fav.params
    const params: StockBacktestParams = {
      ticker: fav.ticker,
      start_date: '2025-01-01',
      end_date: '2026-12-31',
      signal_type: (p.signal_type as string) || 'ema_cross',
      ema_fast: (p.ema_fast as number) || 8,
      ema_slow: (p.ema_slow as number) || 21,
      bar_interval: (p.bar_interval as string) || '5m',
      rsi_period: (p.rsi_period as number) || 0,
      rsi_ob: 70,
      rsi_os: 30,
      orb_minutes: (p.orb_minutes as number) || 15,
      atr_period: (p.atr_period as number) || 0,
      atr_stop_mult: (p.atr_stop_mult as number) || 2.0,
      afternoon_enabled: true,
      quantity: (p.quantity as number) || 2,
      stop_loss_percent: (p.stop_loss_percent as number) || 16,
      profit_target_percent: (p.profit_target_percent as number) || 40,
      trailing_stop_percent: (p.trailing_stop_percent as number) || 20,
      max_hold_minutes: (p.max_hold_minutes as number) || 90,
      min_confluence: (p.min_confluence as number) || 5,
      vol_threshold: (p.vol_threshold as number) || 1.5,
      orb_body_min_pct: (p.orb_body_min_pct as number) || 0,
      orb_vwap_filter: (p.orb_vwap_filter as boolean) || false,
      orb_gap_fade_filter: (p.orb_gap_fade_filter as boolean) || false,
      orb_stop_mult: (p.orb_stop_mult as number) || 1.0,
      orb_target_mult: (p.orb_target_mult as number) || 1.5,
      max_daily_trades: 10,
      max_daily_loss: 2000,
      max_consecutive_losses: 3,
    }
    onLoadBacktest(fav.ticker, params)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 text-secondary">
        <svg className="animate-spin h-6 w-6 mr-3" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        Loading favorites...
      </div>
    )
  }

  if (favorites.length === 0) {
    return (
      <div className="bg-surface rounded-lg p-8 text-center text-secondary">
        No favorites saved yet. Run a backtest and click "Save as Favorite" to save strategies here.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Ticker filter chips */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setFilterTicker(null)}
          className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
            !filterTicker ? 'bg-blue-600 text-white' : 'bg-elevated text-secondary hover:text-heading'
          }`}
        >
          All ({favorites.length})
        </button>
        {tickers.map((t) => {
          const count = favorites.filter((f) => f.ticker === t).length
          return (
            <button
              key={t}
              onClick={() => setFilterTicker(filterTicker === t ? null : t)}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                filterTicker === t ? 'bg-green-600 text-white' : 'bg-elevated text-secondary hover:text-heading'
              }`}
            >
              {t} ({count})
            </button>
          )
        })}
      </div>

      {/* Favorite cards grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filtered.map((fav) => {
          const p = fav.params
          const s = fav.summary
          const signalLabel = SIGNAL_LABELS[(p.signal_type as string)] || (p.signal_type as string)

          return (
            <div key={fav.id} className="bg-surface rounded-lg p-4 space-y-3">
              {/* Header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-heading">{fav.ticker}</span>
                  <span className="text-xs text-secondary">{signalLabel}</span>
                  {isEnabled(fav) && (
                    <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-green-500/15 text-green-400 text-[10px] font-semibold">
                      <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                      LIVE
                    </span>
                  )}
                </div>
                <span className="text-xs text-muted">
                  {new Date(fav.created_at).toLocaleDateString()}
                </span>
              </div>

              {/* Strategy name */}
              <p className="text-sm font-medium text-primary">{fav.strategy_name}</p>

              {/* Metrics from summary */}
              {s && (
                <div className="grid grid-cols-3 gap-2 text-xs">
                  {s.total_pnl != null && (
                    <div>
                      <span className="text-secondary">P&L</span>
                      <p className={`font-semibold ${Number(s.total_pnl) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {formatCurrency(Number(s.total_pnl))}
                      </p>
                    </div>
                  )}
                  {s.win_rate != null && (
                    <div>
                      <span className="text-secondary">Win Rate</span>
                      <p className={`font-semibold ${Number(s.win_rate) >= 50 ? 'text-green-400' : 'text-yellow-400'}`}>
                        {Number(s.win_rate).toFixed(0)}%
                      </p>
                    </div>
                  )}
                  {s.total_trades != null && (
                    <div>
                      <span className="text-secondary">Trades</span>
                      <p className="font-semibold text-blue-400">{s.total_trades}</p>
                    </div>
                  )}
                  {s.profit_factor != null && (
                    <div>
                      <span className="text-secondary">PF</span>
                      <p className={`font-semibold ${Number(s.profit_factor) >= 1 ? 'text-green-400' : 'text-red-400'}`}>
                        {Number(s.profit_factor).toFixed(2)}
                      </p>
                    </div>
                  )}
                  {s.max_drawdown != null && (
                    <div>
                      <span className="text-secondary">MaxDD</span>
                      <p className="font-semibold text-red-400">{formatCurrency(Number(s.max_drawdown))}</p>
                    </div>
                  )}
                  {s.avg_hold_minutes != null && (
                    <div>
                      <span className="text-secondary">Avg Hold</span>
                      <p className="font-semibold text-tertiary">{Number(s.avg_hold_minutes).toFixed(0)}m</p>
                    </div>
                  )}
                </div>
              )}

              {/* Key params */}
              <div className="flex flex-wrap gap-1">
                {p.stop_loss_percent != null && (
                  <span className="px-1.5 py-0.5 rounded bg-red-500/10 text-red-400 text-xs">SL {p.stop_loss_percent}%</span>
                )}
                {p.profit_target_percent != null && (
                  <span className="px-1.5 py-0.5 rounded bg-green-500/10 text-green-400 text-xs">PT {p.profit_target_percent}%</span>
                )}
                {p.trailing_stop_percent != null && (
                  <span className="px-1.5 py-0.5 rounded bg-orange-500/10 text-orange-400 text-xs">Trail {p.trailing_stop_percent}%</span>
                )}
                {p.max_hold_minutes != null && (
                  <span className="px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 text-xs">{p.max_hold_minutes}m</span>
                )}
                {p.ema_fast != null && (
                  <span className="px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 text-xs">EMA {p.ema_fast}/{p.ema_slow}</span>
                )}
              </div>

              {/* Notes */}
              {fav.notes && (
                <p className="text-xs text-muted italic">{fav.notes}</p>
              )}

              {/* Actions */}
              <div className="flex gap-2 pt-1">
                <button
                  onClick={() => handleToggleEnable(fav)}
                  disabled={togglingIds.has(fav.id)}
                  className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                    isEnabled(fav)
                      ? 'bg-green-600 text-white hover:bg-green-700'
                      : 'bg-elevated text-secondary hover:bg-green-600/30 hover:text-green-400'
                  } ${togglingIds.has(fav.id) ? 'opacity-50' : ''}`}
                >
                  {togglingIds.has(fav.id) ? '...' : isEnabled(fav) ? 'Live' : 'Enable'}
                </button>
                <button
                  onClick={() => handleLoad(fav)}
                  className="flex-1 px-3 py-1.5 rounded bg-blue-600/30 hover:bg-blue-600 text-blue-400 hover:text-white text-xs font-medium transition-colors"
                >
                  Load & Run Backtest
                </button>
                <button
                  onClick={() => handleRemove(fav.id)}
                  className="px-3 py-1.5 rounded bg-red-600/20 hover:bg-red-600 text-red-400 hover:text-white text-xs font-medium transition-colors"
                >
                  Remove
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
