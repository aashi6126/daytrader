import { useState, useEffect, useRef, useCallback } from 'react'
import { useLocation } from 'react-router-dom'
import {
  BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import {
  runBacktest, runOptimization,
  type BacktestParams, type BacktestResponse, type BacktestTrade,
  type OptimizeParams, type OptimizeResponse, type OptimizeResultEntry,
  type SignalType, ALL_SIGNAL_TYPES,
} from '../api/backtest'
import {
  getAvailableTickers, runStockOptimization, runStockBacktest,
  searchSymbols, downloadSymbolData, saveFavorite,
  type TickerInfo, type StockBacktestParams, type SearchResult,
} from '../api/stockBacktest'
import { formatCurrency } from '../utils/format'
import { useChartColors } from '../hooks/useChartColors'
import TopSetups from './TopSetups'
import Favorites from './Favorites'

function defaultParams(): BacktestParams {
  const end = new Date()
  const start = new Date()
  start.setDate(end.getDate() - 180)
  return {
    start_date: start.toISOString().slice(0, 10),
    end_date: end.toISOString().slice(0, 10),
    signal_type: 'all',
    ema_fast: 8,
    ema_slow: 21,
    bar_interval: '5m',
    rsi_period: 0,
    rsi_ob: 70,
    rsi_os: 30,
    orb_minutes: 15,
    atr_period: 0,
    atr_stop_mult: 2.0,
    afternoon_enabled: true,
    entry_limit_below_percent: 5.0,
    quantity: 2,
    delta_target: 0.35,
    stop_loss_percent: 16.0,
    profit_target_percent: 40.0,
    trailing_stop_percent: 20.0,
    trailing_stop_after_scale_out_percent: 10.0,
    max_hold_minutes: 90,
    scale_out_enabled: true,
    breakeven_trigger_percent: 10.0,
    min_confluence: 5,
    vol_threshold: 1.5,
    max_daily_trades: 10,
    max_daily_loss: 2000,
    max_consecutive_losses: 3,
    entry_confirm_minutes: 0,
  }
}

function formatDateShort(dateStr: string) {
  const d = new Date(dateStr + 'T12:00:00')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function formatDt(iso: string) {
  const d = new Date(iso)
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' })
}

const REASON_COLORS: Record<string, string> = {
  STOP_LOSS: 'bg-red-500/20 text-red-400',
  TRAILING_STOP: 'bg-orange-500/20 text-orange-400',
  PROFIT_TARGET: 'bg-green-500/20 text-green-400',
  MAX_HOLD_TIME: 'bg-yellow-500/20 text-yellow-400',
  TIME_BASED: 'bg-blue-500/20 text-blue-400',
}

const METRIC_OPTIONS = [
  { value: 'pro', label: 'Pro (PF + Exit Quality + Recovery)' },
  { value: 'composite', label: 'Composite (PF x \u221Atrades)' },
  { value: 'total_pnl', label: 'Total P&L' },
  { value: 'profit_factor', label: 'Profit Factor' },
  { value: 'win_rate', label: 'Win Rate' },
  { value: 'risk_adjusted', label: 'Risk Adjusted (PnL / DD)' },
  { value: 'sharpe', label: 'Sharpe (Risk Adj x \u221Atrades)' },
] as const

function mergeBacktestResults(results: BacktestResponse[]): BacktestResponse {
  const allTrades = results.flatMap((r) => r.trades)
  const dayMap = new Map<string, { pnl: number; total: number; wins: number; losses: number }>()
  for (const r of results) {
    for (const d of r.days) {
      const existing = dayMap.get(d.trade_date)
      if (existing) {
        existing.pnl += d.pnl
        existing.total += d.total_trades
        existing.wins += d.winning_trades
        existing.losses += d.losing_trades
      } else {
        dayMap.set(d.trade_date, { pnl: d.pnl, total: d.total_trades, wins: d.winning_trades, losses: d.losing_trades })
      }
    }
  }
  const days = [...dayMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([trade_date, d]) => ({ trade_date, pnl: d.pnl, total_trades: d.total, winning_trades: d.wins, losing_trades: d.losses }))

  const wins = allTrades.filter((t) => (t.pnl_dollars ?? 0) > 0)
  const losses = allTrades.filter((t) => (t.pnl_dollars ?? 0) <= 0)
  const totalPnl = allTrades.reduce((s, t) => s + (t.pnl_dollars ?? 0), 0)
  const totalWins = wins.reduce((s, t) => s + (t.pnl_dollars ?? 0), 0)
  const totalLosses = Math.abs(losses.reduce((s, t) => s + (t.pnl_dollars ?? 0), 0))
  const exitReasons: Record<string, number> = {}
  for (const t of allTrades) if (t.exit_reason) exitReasons[t.exit_reason] = (exitReasons[t.exit_reason] || 0) + 1

  // Max drawdown from cumulative equity curve
  let peak = 0, maxDd = 0, cum = 0
  for (const d of days) {
    cum += d.pnl
    if (cum > peak) peak = cum
    const dd = peak - cum
    if (dd > maxDd) maxDd = dd
  }

  return {
    summary: {
      total_pnl: totalPnl,
      total_trades: allTrades.length,
      winning_trades: wins.length,
      losing_trades: losses.length,
      win_rate: allTrades.length > 0 ? Math.round(wins.length / allTrades.length * 1000) / 10 : 0,
      avg_win: wins.length > 0 ? totalWins / wins.length : 0,
      avg_loss: losses.length > 0 ? totalLosses / losses.length : 0,
      largest_win: wins.length > 0 ? Math.max(...wins.map((t) => t.pnl_dollars ?? 0)) : 0,
      largest_loss: losses.length > 0 ? Math.min(...losses.map((t) => t.pnl_dollars ?? 0)) : 0,
      max_drawdown: maxDd,
      profit_factor: totalLosses > 0 ? totalWins / totalLosses : totalWins > 0 ? 999 : 0,
      avg_hold_minutes: allTrades.length > 0 ? allTrades.reduce((s, t) => s + (t.hold_minutes ?? 0), 0) / allTrades.length : 0,
      exit_reasons: exitReasons,
    },
    days,
    trades: allTrades.sort((a, b) => a.entry_time.localeCompare(b.entry_time)),
  }
}

export default function Backtest() {
  const [activeTab, setActiveTab] = useState<'setups' | 'backtest' | 'optimize' | 'favorites'>('setups')
  const [params, setParams] = useState<BacktestParams>(defaultParams)

  // Backtest state
  const [backtestTicker, setBacktestTicker] = useState('SPY')
  const [result, setResult] = useState<BacktestResponse | null>(null)
  const [signalBreakdown, setSignalBreakdown] = useState<Record<string, BacktestResponse>>({})
  const [selectedSignal, setSelectedSignal] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Save favorite modal
  const [showSaveModal, setShowSaveModal] = useState(false)
  const [saveName, setSaveName] = useState('')
  const [saveNotes, setSaveNotes] = useState('')
  const [saving, setSaving] = useState(false)

  // Optimizer state
  const [optimizeResult, setOptimizeResult] = useState<OptimizeResponse | null>(null)
  const [optimizing, setOptimizing] = useState(false)
  const [optimizeError, setOptimizeError] = useState<string | null>(null)
  const [numIterations, setNumIterations] = useState(200)
  const [targetMetric, setTargetMetric] = useState<OptimizeParams['target_metric']>('pro')
  const [optimizeTicker, setOptimizeTicker] = useState('SPY')
  const [optimizeInterval, setOptimizeInterval] = useState('5m')
  const [availableTickers, setAvailableTickers] = useState<TickerInfo[]>([])

  const refreshTickers = useCallback(() => {
    getAvailableTickers().then(setAvailableTickers).catch(() => {})
  }, [])

  useEffect(() => { refreshTickers() }, [refreshTickers])

  // Receive params from TopSetups "Edit & Test" navigation
  const location = useLocation()
  useEffect(() => {
    const setup = (location.state as { fromSetup?: { ticker: string; timeframe: string; params: Record<string, number | string | boolean> } })?.fromSetup
    if (!setup) return
    const p = setup.params
    setBacktestTicker(setup.ticker)
    setParams((prev) => ({
      ...prev,
      signal_type: (p.signal_type as BacktestParams['signal_type']) || prev.signal_type,
      ema_fast: (p.ema_fast as number) || prev.ema_fast,
      ema_slow: (p.ema_slow as number) || prev.ema_slow,
      bar_interval: setup.timeframe || prev.bar_interval,
      stop_loss_percent: (p.stop_loss_percent as number) || prev.stop_loss_percent,
      profit_target_percent: (p.profit_target_percent as number) || prev.profit_target_percent,
      trailing_stop_percent: (p.trailing_stop_percent as number) || prev.trailing_stop_percent,
      trailing_stop_after_scale_out_percent: (p.trailing_stop_after_scale_out_percent as number) || prev.trailing_stop_after_scale_out_percent,
      delta_target: (p.delta_target as number) || prev.delta_target,
      max_hold_minutes: (p.max_hold_minutes as number) || prev.max_hold_minutes,
      rsi_period: (p.rsi_period as number) ?? prev.rsi_period,
      atr_period: (p.atr_period as number) ?? prev.atr_period,
      atr_stop_mult: (p.atr_stop_mult as number) || prev.atr_stop_mult,
      orb_minutes: (p.orb_minutes as number) || prev.orb_minutes,
      min_confluence: (p.min_confluence as number) || prev.min_confluence,
      vol_threshold: (p.vol_threshold as number) || prev.vol_threshold,
      max_daily_trades: (p.max_daily_trades as number) || prev.max_daily_trades,
      max_daily_loss: (p.max_daily_loss as number) || prev.max_daily_loss,
      max_consecutive_losses: (p.max_consecutive_losses as number) || prev.max_consecutive_losses,
      entry_confirm_minutes: (p.entry_confirm_minutes as number) ?? prev.entry_confirm_minutes,
    }))
    setActiveTab('backtest')
    setResult(null)
    // Clear navigation state so refresh doesn't re-apply
    window.history.replaceState({}, '')
  }, [location.state])

  const set = <K extends keyof BacktestParams>(k: K, v: BacktestParams[K]) =>
    setParams((p) => ({ ...p, [k]: v }))

  const isSpy = backtestTicker === 'SPY'

  const runSingle = (signalType: SignalType): Promise<BacktestResponse> => {
    const p = { ...params, signal_type: signalType as BacktestParams['signal_type'] }
    return isSpy
      ? runBacktest(p)
      : runStockBacktest({
          ticker: backtestTicker,
          start_date: p.start_date,
          end_date: p.end_date,
          signal_type: signalType,
          ema_fast: p.ema_fast,
          ema_slow: p.ema_slow,
          bar_interval: p.bar_interval,
          rsi_period: p.rsi_period,
          rsi_ob: p.rsi_ob,
          rsi_os: p.rsi_os,
          orb_minutes: p.orb_minutes,
          atr_period: p.atr_period,
          atr_stop_mult: p.atr_stop_mult,
          afternoon_enabled: p.afternoon_enabled,
          quantity: p.quantity,
          stop_loss_percent: p.stop_loss_percent,
          profit_target_percent: p.profit_target_percent,
          trailing_stop_percent: p.trailing_stop_percent,
          max_hold_minutes: p.max_hold_minutes,
          min_confluence: p.min_confluence,
          vol_threshold: p.vol_threshold,
          orb_body_min_pct: 0.4,
          orb_vwap_filter: true,
          orb_gap_fade_filter: true,
          orb_stop_mult: 1.0,
          orb_target_mult: 1.5,
          max_daily_trades: p.max_daily_trades,
          max_daily_loss: p.max_daily_loss,
          max_consecutive_losses: p.max_consecutive_losses,
          entry_confirm_minutes: p.entry_confirm_minutes,
        }).then((stockResult): BacktestResponse => ({
          summary: stockResult.summary,
          days: stockResult.days,
          trades: stockResult.trades.map((t) => ({
            ...t,
            scaled_out: false,
            scaled_out_price: null,
          })),
        }))
  }

  const run = () => {
    setLoading(true)
    setError(null)
    setSignalBreakdown({})
    setSelectedSignal(null)

    if (params.signal_type === 'all') {
      Promise.all(ALL_SIGNAL_TYPES.map((st) => runSingle(st).then((r) => [st, r] as const)))
        .then((pairs) => {
          const breakdown: Record<string, BacktestResponse> = {}
          const results: BacktestResponse[] = []
          for (const [st, r] of pairs) {
            breakdown[st] = r
            results.push(r)
          }
          setSignalBreakdown(breakdown)
          setResult(mergeBacktestResults(results))
        })
        .catch((err) => setError(err.response?.data?.detail || 'Backtest failed'))
        .finally(() => setLoading(false))
    } else {
      runSingle(params.signal_type as SignalType)
        .then(setResult)
        .catch((err) => setError(err.response?.data?.detail || 'Backtest failed'))
        .finally(() => setLoading(false))
    }
  }

  const optimize = () => {
    setOptimizing(true)
    setOptimizeError(null)

    const promise: Promise<OptimizeResponse> = optimizeTicker === 'SPY'
      ? runOptimization({
          start_date: params.start_date,
          end_date: params.end_date,
          bar_interval: optimizeInterval as '5m' | '1m',
          num_iterations: numIterations,
          target_metric: targetMetric,
          top_n: 10,
          afternoon_enabled: params.afternoon_enabled,
          scale_out_enabled: params.scale_out_enabled,
          quantity: params.quantity,
        })
      : runStockOptimization({
          ticker: optimizeTicker,
          bar_interval: optimizeInterval,
          num_iterations: numIterations,
          target_metric: targetMetric,
          top_n: 10,
          quantity: params.quantity,
        }).then((stockResult): OptimizeResponse => ({
          total_combinations_tested: stockResult.total_combinations_tested,
          elapsed_seconds: stockResult.elapsed_seconds,
          target_metric: stockResult.target_metric,
          train_start: null,
          train_end: null,
          test_start: null,
          test_end: null,
          results: stockResult.results.map((r) => ({
            rank: r.rank,
            params: r.params,
            total_pnl: r.total_pnl,
            total_trades: r.total_trades,
            win_rate: r.win_rate,
            profit_factor: r.profit_factor,
            max_drawdown: r.max_drawdown,
            avg_hold_minutes: r.avg_hold_minutes,
            score: r.score,
            exit_reasons: r.exit_reasons,
            oos_total_pnl: null,
            oos_total_trades: null,
            oos_win_rate: null,
            oos_profit_factor: null,
            oos_score: null,
          })),
        }))

    promise
      .then(setOptimizeResult)
      .catch((err) => setOptimizeError(err.response?.data?.detail || 'Optimization failed'))
      .finally(() => setOptimizing(false))
  }

  const applyParams = (entry: OptimizeResultEntry) => {
    const p = entry.params
    setParams((prev) => ({
      ...prev,
      signal_type: p.signal_type as BacktestParams['signal_type'],
      ema_fast: p.ema_fast as number,
      ema_slow: p.ema_slow as number,
      stop_loss_percent: p.stop_loss_percent as number,
      profit_target_percent: p.profit_target_percent as number,
      trailing_stop_percent: p.trailing_stop_percent as number,
      trailing_stop_after_scale_out_percent: (p.trailing_stop_after_scale_out_percent as number) || 10.0,
      delta_target: p.delta_target as number,
      max_hold_minutes: p.max_hold_minutes as number,
      rsi_period: (p.rsi_period as number) || 0,
      atr_period: (p.atr_period as number) || 0,
      atr_stop_mult: (p.atr_stop_mult as number) || 2.0,
      orb_minutes: (p.orb_minutes as number) || 15,
      min_confluence: (p.min_confluence as number) || 5,
      vol_threshold: (p.vol_threshold as number) || 1.5,
    }))
    setActiveTab('backtest')
  }

  // Called from TopSetups when user clicks the SPY tile
  const applySpySetup = (spyParams: Record<string, number | string | boolean>) => {
    setParams((prev) => ({
      ...prev,
      signal_type: (spyParams.signal_type as BacktestParams['signal_type']) || prev.signal_type,
      ema_fast: (spyParams.ema_fast as number) || prev.ema_fast,
      ema_slow: (spyParams.ema_slow as number) || prev.ema_slow,
      stop_loss_percent: (spyParams.stop_loss_percent as number) || prev.stop_loss_percent,
      profit_target_percent: (spyParams.profit_target_percent as number) || prev.profit_target_percent,
      trailing_stop_percent: (spyParams.trailing_stop_percent as number) || prev.trailing_stop_percent,
      trailing_stop_after_scale_out_percent: (spyParams.trailing_stop_after_scale_out_percent as number) || prev.trailing_stop_after_scale_out_percent,
      delta_target: (spyParams.delta_target as number) || prev.delta_target,
      max_hold_minutes: (spyParams.max_hold_minutes as number) || prev.max_hold_minutes,
      rsi_period: (spyParams.rsi_period as number) ?? prev.rsi_period,
      atr_period: (spyParams.atr_period as number) ?? prev.atr_period,
      atr_stop_mult: (spyParams.atr_stop_mult as number) || prev.atr_stop_mult,
      orb_minutes: (spyParams.orb_minutes as number) || prev.orb_minutes,
      min_confluence: (spyParams.min_confluence as number) || prev.min_confluence,
      vol_threshold: (spyParams.vol_threshold as number) || prev.vol_threshold,
    }))
    setActiveTab('backtest')
  }

  const handleSaveFavorite = async () => {
    if (!saveName.trim() || !result) return
    setSaving(true)
    try {
      await saveFavorite({
        ticker: backtestTicker,
        strategy_name: saveName.trim(),
        params: {
          ...params,
          bar_interval: params.bar_interval,
        },
        summary: {
          total_pnl: result.summary.total_pnl,
          total_trades: result.summary.total_trades,
          win_rate: result.summary.win_rate,
          profit_factor: result.summary.profit_factor,
          max_drawdown: result.summary.max_drawdown,
          avg_hold_minutes: result.summary.avg_hold_minutes,
        },
        notes: saveNotes.trim() || undefined,
      })
      setShowSaveModal(false)
      setSaveName('')
      setSaveNotes('')
    } catch (err) {
      console.error('Failed to save favorite:', err)
    } finally {
      setSaving(false)
    }
  }

  const handleLoadFavorite = (ticker: string, btParams: StockBacktestParams) => {
    setBacktestTicker(ticker)
    setParams((prev) => ({
      ...prev,
      start_date: btParams.start_date,
      end_date: btParams.end_date,
      signal_type: btParams.signal_type as BacktestParams['signal_type'],
      ema_fast: btParams.ema_fast,
      ema_slow: btParams.ema_slow,
      bar_interval: btParams.bar_interval as '5m' | '1m',
      rsi_period: btParams.rsi_period,
      stop_loss_percent: btParams.stop_loss_percent,
      profit_target_percent: btParams.profit_target_percent,
      trailing_stop_percent: btParams.trailing_stop_percent,
      max_hold_minutes: btParams.max_hold_minutes,
      min_confluence: btParams.min_confluence,
      vol_threshold: btParams.vol_threshold,
      quantity: btParams.quantity,
    }))
    setResult(null)
    setActiveTab('backtest')
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <div className="flex items-center gap-4">
        <h1 className="text-2xl font-bold">Backtest</h1>
        <div className="flex bg-elevated rounded-lg p-0.5">
          <button
            onClick={() => setActiveTab('setups')}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'setups' ? 'bg-green-600 text-white' : 'text-secondary hover:text-primary'
            }`}
          >
            Top Setups
          </button>
          <button
            onClick={() => setActiveTab('backtest')}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'backtest' ? 'bg-blue-600 text-white' : 'text-secondary hover:text-primary'
            }`}
          >
            Backtest
          </button>
          <button
            onClick={() => setActiveTab('optimize')}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'optimize' ? 'bg-purple-600 text-white' : 'text-secondary hover:text-primary'
            }`}
          >
            Optimize
          </button>
          <button
            onClick={() => setActiveTab('favorites')}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'favorites' ? 'bg-yellow-600 text-white' : 'text-secondary hover:text-primary'
            }`}
          >
            Favorites
          </button>
        </div>
      </div>

      {activeTab === 'setups' && (
        <TopSetups onApplySpyParams={applySpySetup} />
      )}

      {activeTab === 'backtest' && (
        <>
          {/* Params form */}
          <div className="bg-surface rounded-lg p-6">
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4 text-sm">
              <label className="space-y-1">
                <span className="text-secondary">Ticker</span>
                <TickerSearch
                  value={backtestTicker}
                  onChange={(t) => {
                    setBacktestTicker(t)
                    setResult(null)
                    if (t !== 'SPY') {
                      const info = availableTickers.find((x) => x.ticker === t)
                      if (info && !info.timeframes.includes(params.bar_interval)) {
                        set('bar_interval', (info.timeframes[0] || '5m') as '5m' | '1m')
                      }
                    } else if (!['5m', '1m'].includes(params.bar_interval)) {
                      set('bar_interval', '5m')
                    }
                  }}
                  availableTickers={availableTickers}
                  onTickerDownloaded={refreshTickers}
                />
              </label>
              <label className="space-y-1">
                <span className="text-secondary">Start Date</span>
                <input type="date" value={params.start_date}
                  onChange={(e) => set('start_date', e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
              <label className="space-y-1">
                <span className="text-secondary">End Date</span>
                <input type="date" value={params.end_date}
                  onChange={(e) => set('end_date', e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
              <label className="space-y-1">
                <span className="text-secondary">Signal</span>
                <select value={params.signal_type}
                  onChange={(e) => set('signal_type', e.target.value as SignalType | 'all')}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading">
                  <option value="all">All Signals</option>
                  <option value="ema_cross">EMA Cross</option>
                  <option value="vwap_cross">VWAP Cross</option>
                  <option value="ema_vwap">EMA + VWAP</option>
                  <option value="orb">ORB Breakout</option>
                  <option value="vwap_rsi">VWAP + RSI</option>
                  <option value="bb_squeeze">BB Squeeze</option>
                  <option value="rsi_reversal">RSI Reversal</option>
                  <option value="confluence">Confluence (6-factor)</option>
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-secondary">Interval</span>
                <select value={params.bar_interval}
                  onChange={(e) => set('bar_interval', e.target.value as '5m' | '1m')}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading">
                  {isSpy ? (
                    <>
                      <option value="5m">5 min</option>
                      <option value="1m">1 min</option>
                    </>
                  ) : (
                    (availableTickers.find((t) => t.ticker === backtestTicker)?.timeframes || ['5m']).map((tf) => (
                      <option key={tf} value={tf}>{tf.replace('m', ' min')}</option>
                    ))
                  )}
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-secondary">EMA Fast</span>
                <input type="number" value={params.ema_fast}
                  onChange={(e) => set('ema_fast', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
              <label className="space-y-1">
                <span className="text-secondary">EMA Slow</span>
                <input type="number" value={params.ema_slow}
                  onChange={(e) => set('ema_slow', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>

              <label className="space-y-1">
                <span className="text-secondary">RSI Period</span>
                <input type="number" value={params.rsi_period} min={0} max={50}
                  onChange={(e) => set('rsi_period', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
              <label className="space-y-1">
                <span className="text-secondary">ATR Period</span>
                <input type="number" value={params.atr_period} min={0} max={50}
                  onChange={(e) => set('atr_period', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
              <label className="space-y-1">
                <span className="text-secondary">Min Confluence</span>
                <input type="number" value={params.min_confluence} min={3} max={6}
                  onChange={(e) => set('min_confluence', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
              <label className="space-y-1">
                <span className="text-secondary">Vol Threshold</span>
                <input type="number" step="0.5" value={params.vol_threshold} min={1.0} max={3.0}
                  onChange={(e) => set('vol_threshold', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
              <label className="space-y-1">
                <span className="text-secondary">Confirm Min</span>
                <select value={params.entry_confirm_minutes}
                  onChange={(e) => set('entry_confirm_minutes', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading">
                  <option value={0}>0 (Immediate)</option>
                  <option value={1}>1 min</option>
                  <option value={2}>2 min</option>
                  <option value={3}>3 min</option>
                  <option value={5}>5 min</option>
                  <option value={15}>15 min</option>
                </select>
              </label>

              <label className="space-y-1">
                <span className="text-secondary">Stop Loss %</span>
                <input type="number" step="1" value={params.stop_loss_percent}
                  onChange={(e) => set('stop_loss_percent', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
              <label className="space-y-1">
                <span className="text-secondary">Profit Target %</span>
                <input type="number" step="5" value={params.profit_target_percent}
                  onChange={(e) => set('profit_target_percent', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
              <label className="space-y-1">
                <span className="text-secondary">Trailing Stop %</span>
                <input type="number" step="5" value={params.trailing_stop_percent}
                  onChange={(e) => set('trailing_stop_percent', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
              {isSpy && (
                <label className="space-y-1">
                  <span className="text-secondary">Trail SO %</span>
                  <input type="number" step="5" value={params.trailing_stop_after_scale_out_percent}
                    onChange={(e) => set('trailing_stop_after_scale_out_percent', +e.target.value)}
                    className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
                </label>
              )}
              <label className="space-y-1">
                <span className="text-secondary">Max Hold (min)</span>
                <input type="number" step="10" value={params.max_hold_minutes}
                  onChange={(e) => set('max_hold_minutes', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
              {isSpy && (
                <label className="space-y-1">
                  <span className="text-secondary">Delta Target</span>
                  <input type="number" step="0.05" value={params.delta_target}
                    onChange={(e) => set('delta_target', +e.target.value)}
                    className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
                </label>
              )}
              <label className="space-y-1">
                <span className="text-secondary">Quantity</span>
                <input type="number" value={params.quantity}
                  onChange={(e) => set('quantity', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
            </div>

            <div className="flex items-center gap-6 mt-4">
              {isSpy && (
                <label className="flex items-center gap-2 text-sm text-tertiary">
                  <input type="checkbox" checked={params.scale_out_enabled}
                    onChange={(e) => set('scale_out_enabled', e.target.checked)}
                    className="rounded bg-elevated" />
                  Scale-Out
                </label>
              )}
              <label className="flex items-center gap-2 text-sm text-tertiary">
                <input type="checkbox" checked={params.afternoon_enabled}
                  onChange={(e) => set('afternoon_enabled', e.target.checked)}
                  className="rounded bg-elevated" />
                Afternoon Window
              </label>
              {isSpy && (
                <label className="flex items-center gap-2 text-sm text-tertiary">
                  <input type="checkbox" checked={params.breakeven_trigger_percent > 0}
                    onChange={(e) => set('breakeven_trigger_percent', e.target.checked ? 10.0 : 0)}
                    className="rounded bg-elevated" />
                  Breakeven Stop
                </label>
              )}

              <div className="ml-auto flex gap-2">
                {result && !loading && (
                  <button
                    onClick={() => { setSaveName(`${backtestTicker} ${params.signal_type}`); setShowSaveModal(true) }}
                    className="px-4 py-2 rounded bg-yellow-600/30 hover:bg-yellow-600 text-yellow-400 hover:text-white font-medium text-sm transition-colors"
                  >
                    Save as Favorite
                  </button>
                )}
                <button
                  onClick={run}
                  disabled={loading}
                  className="px-6 py-2 rounded bg-blue-600 hover:bg-blue-500 text-white font-medium text-sm disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {loading ? 'Running...' : `Run ${backtestTicker} Backtest`}
                </button>
              </div>
            </div>

            {error && (
              <p className="mt-3 text-sm text-red-400">{error}</p>
            )}
          </div>

          {loading && (
            <div className="flex items-center justify-center py-12 text-secondary">
              <svg className="animate-spin h-6 w-6 mr-3" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              Running {backtestTicker} backtest simulation...
            </div>
          )}

          {result && !loading && (() => {
            const drillResult = selectedSignal && signalBreakdown[selectedSignal] ? signalBreakdown[selectedSignal] : null
            const displayResult = drillResult || result
            return (
              <>
                {!drillResult && <SummaryCards summary={result.summary} />}
                {Object.keys(signalBreakdown).length > 0 && !drillResult && (
                  <SignalBreakdownTable breakdown={signalBreakdown} onSelect={setSelectedSignal} />
                )}
                {drillResult && (
                  <div className="flex items-center gap-3 mb-2">
                    <button
                      onClick={() => setSelectedSignal(null)}
                      className="px-3 py-1.5 rounded text-sm font-medium bg-elevated hover:bg-hover text-secondary hover:text-primary"
                    >
                      &larr; All Signals
                    </button>
                    <span className="text-heading font-medium">
                      {SIGNAL_LABELS[selectedSignal!] || selectedSignal}
                    </span>
                    <span className={`text-sm ${drillResult.summary.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {formatCurrency(drillResult.summary.total_pnl)}
                    </span>
                    <span className="text-sm text-secondary">{drillResult.summary.total_trades} trades</span>
                  </div>
                )}
                {drillResult && <SummaryCards summary={drillResult.summary} />}
                <DailyPnLChart days={displayResult.days} totalPnl={displayResult.summary.total_pnl} />
                <ExitReasons reasons={displayResult.summary.exit_reasons} />
                <TradeList trades={displayResult.trades} />
              </>
            )
          })()}
        </>
      )}

      {activeTab === 'optimize' && (
        <>
          <div className="bg-surface rounded-lg p-6">
            <h2 className="text-lg font-semibold mb-4">
              {optimizeTicker} Optimizer
              {optimizeTicker === 'SPY' && <span className="text-sm text-secondary font-normal ml-2">(0DTE Options)</span>}
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4 text-sm">
              <label className="space-y-1">
                <span className="text-secondary">Ticker</span>
                <TickerSearch
                  value={optimizeTicker}
                  onChange={(t) => {
                    setOptimizeTicker(t)
                    setOptimizeResult(null)
                    if (t !== 'SPY') {
                      const info = availableTickers.find((x) => x.ticker === t)
                      if (info && !info.timeframes.includes(optimizeInterval)) {
                        setOptimizeInterval(info.timeframes[0] || '5m')
                      }
                    } else if (!['5m', '1m'].includes(optimizeInterval)) {
                      setOptimizeInterval('5m')
                    }
                  }}
                  availableTickers={availableTickers}
                  onTickerDownloaded={refreshTickers}
                />
              </label>

              {optimizeTicker === 'SPY' && (
                <>
                  <label className="space-y-1">
                    <span className="text-secondary">Start Date</span>
                    <input type="date" value={params.start_date}
                      onChange={(e) => set('start_date', e.target.value)}
                      className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
                  </label>
                  <label className="space-y-1">
                    <span className="text-secondary">End Date</span>
                    <input type="date" value={params.end_date}
                      onChange={(e) => set('end_date', e.target.value)}
                      className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
                  </label>
                </>
              )}

              <label className="space-y-1">
                <span className="text-secondary">Interval</span>
                <select value={optimizeInterval}
                  onChange={(e) => setOptimizeInterval(e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading">
                  {optimizeTicker === 'SPY' ? (
                    <>
                      <option value="5m">5 min</option>
                      <option value="1m">1 min</option>
                    </>
                  ) : (
                    (availableTickers.find((t) => t.ticker === optimizeTicker)?.timeframes || ['5m']).map((tf) => (
                      <option key={tf} value={tf}>{tf.replace('m', ' min')}</option>
                    ))
                  )}
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-secondary">Iterations</span>
                <input type="number" min={10} max={5000} step={50} value={numIterations}
                  onChange={(e) => setNumIterations(+e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
              <label className="space-y-1">
                <span className="text-secondary">Optimize For</span>
                <select value={targetMetric}
                  onChange={(e) => setTargetMetric(e.target.value as OptimizeParams['target_metric'])}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading">
                  {METRIC_OPTIONS.map((m) => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-secondary">Quantity</span>
                <input type="number" value={params.quantity}
                  onChange={(e) => set('quantity', +e.target.value)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading" />
              </label>
            </div>

            <div className="flex items-center gap-6 mt-4">
              {optimizeTicker === 'SPY' && (
                <>
                  <label className="flex items-center gap-2 text-sm text-tertiary">
                    <input type="checkbox" checked={params.scale_out_enabled}
                      onChange={(e) => set('scale_out_enabled', e.target.checked)}
                      className="rounded bg-elevated" />
                    Scale-Out
                  </label>
                  <label className="flex items-center gap-2 text-sm text-tertiary">
                    <input type="checkbox" checked={params.afternoon_enabled}
                      onChange={(e) => set('afternoon_enabled', e.target.checked)}
                      className="rounded bg-elevated" />
                    Afternoon Window
                  </label>
                </>
              )}

              <button
                onClick={optimize}
                disabled={optimizing}
                className="ml-auto px-6 py-2 rounded bg-purple-600 hover:bg-purple-500 text-white font-medium text-sm disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {optimizing ? 'Optimizing...' : `Run ${optimizeTicker} Optimization`}
              </button>
            </div>

            {optimizeError && (
              <p className="mt-3 text-sm text-red-400">{optimizeError}</p>
            )}
          </div>

          {optimizing && (
            <div className="flex items-center justify-center py-12 text-secondary">
              <svg className="animate-spin h-6 w-6 mr-3" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              Testing {numIterations} parameter combinations for {optimizeTicker}... This may take 1-2 minutes.
            </div>
          )}

          {optimizeResult && !optimizing && (
            <OptimizerResults
              result={optimizeResult}
              onApply={optimizeTicker === 'SPY' ? applyParams : undefined}
              ticker={optimizeTicker}
              interval={optimizeInterval}
            />
          )}
        </>
      )}

      {activeTab === 'favorites' && (
        <Favorites onLoadBacktest={handleLoadFavorite} />
      )}

      {/* Save as Favorite modal */}
      {showSaveModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowSaveModal(false)}>
          <div className="bg-surface rounded-lg p-6 w-full max-w-md space-y-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold">Save as Favorite</h3>
            <label className="block space-y-1">
              <span className="text-sm text-secondary">Strategy Name</span>
              <input
                type="text"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                placeholder={`${backtestTicker} ${params.signal_type}`}
                className="w-full bg-elevated rounded px-3 py-2 text-heading"
                autoFocus
              />
            </label>
            <label className="block space-y-1">
              <span className="text-sm text-secondary">Notes (optional)</span>
              <textarea
                value={saveNotes}
                onChange={(e) => setSaveNotes(e.target.value)}
                rows={2}
                className="w-full bg-elevated rounded px-3 py-2 text-heading resize-none"
              />
            </label>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowSaveModal(false)}
                className="px-4 py-2 text-sm text-secondary hover:text-heading"
              >
                Cancel
              </button>
              <button
                onClick={handleSaveFavorite}
                disabled={saving || !saveName.trim()}
                className="px-4 py-2 rounded bg-yellow-600 hover:bg-yellow-500 text-white text-sm font-medium disabled:opacity-50"
              >
                {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


// ── Searchable Ticker Input ─────────────────────────────────────

function TickerSearch({ value, onChange, availableTickers, onTickerDownloaded }: {
  value: string
  onChange: (ticker: string) => void
  availableTickers: TickerInfo[]
  onTickerDownloaded: () => void
}) {
  const [query, setQuery] = useState(value)
  const [results, setResults] = useState<SearchResult[]>([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [downloading, setDownloading] = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout>>()
  const containerRef = useRef<HTMLDivElement>(null)

  // Quick picks = downloaded tickers
  const quickPicks = availableTickers.map((t) => t.ticker)

  // Debounced search
  useEffect(() => {
    if (!query || query.length < 1) {
      setResults([])
      return
    }
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      searchSymbols(query).then(setResults).catch(() => setResults([]))
    }, 250)
    return () => clearTimeout(timerRef.current)
  }, [query])

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowDropdown(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const selectTicker = (ticker: string) => {
    setQuery(ticker)
    onChange(ticker)
    setShowDropdown(false)
  }

  const handleDownload = async (symbol: string) => {
    setDownloading(symbol)
    try {
      const resp = await downloadSymbolData(symbol)
      if (resp.ok) {
        onTickerDownloaded()
        selectTicker(symbol)
      }
    } catch (err) {
      console.error('Download failed:', err)
    } finally {
      setDownloading(null)
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value.toUpperCase())
          setShowDropdown(true)
        }}
        onFocus={() => setShowDropdown(true)}
        placeholder="Search ticker..."
        className="w-full bg-elevated rounded px-2 py-1.5 text-heading"
      />
      {showDropdown && (
        <div className="absolute z-20 top-full left-0 right-0 mt-1 bg-elevated border border-subtle rounded-lg shadow-lg max-h-64 overflow-y-auto">
          {/* Quick picks */}
          {!query && quickPicks.length > 0 && (
            <div className="p-2 border-b border-subtle">
              <p className="text-xs text-muted mb-1">Downloaded</p>
              <div className="flex flex-wrap gap-1">
                {['SPY', ...quickPicks].map((t) => (
                  <button
                    key={t}
                    onClick={() => selectTicker(t)}
                    className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
                      t === value ? 'bg-blue-600 text-white' : 'bg-surface text-secondary hover:text-heading'
                    }`}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
          )}
          {/* Search results */}
          {query && results.map((r) => (
            <div
              key={r.symbol}
              className="flex items-center justify-between px-3 py-2 hover:bg-hover cursor-pointer"
              onClick={() => r.has_data ? selectTicker(r.symbol) : undefined}
            >
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm">{r.symbol}</span>
                {r.has_data && <span className="text-xs text-green-400">Ready</span>}
              </div>
              {!r.has_data && (
                <button
                  onClick={(e) => { e.stopPropagation(); handleDownload(r.symbol) }}
                  disabled={downloading === r.symbol}
                  className="px-2 py-0.5 rounded bg-blue-600/30 hover:bg-blue-600 text-blue-400 hover:text-white text-xs font-medium transition-colors disabled:opacity-50"
                >
                  {downloading === r.symbol ? 'Downloading...' : 'Download'}
                </button>
              )}
            </div>
          ))}
          {query && results.length === 0 && (
            <p className="px-3 py-2 text-xs text-muted">No matches</p>
          )}
        </div>
      )}
    </div>
  )
}


// ── Optimizer Results ────────────────────────────────────────────

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

function OptimizerResults({ result, onApply, ticker = 'SPY', interval = '5m' }: {
  result: OptimizeResponse;
  onApply?: (e: OptimizeResultEntry) => void;
  ticker?: string;
  interval?: string;
}) {
  const metricLabel = METRIC_OPTIONS.find((m) => m.value === result.target_metric)?.label ?? result.target_metric
  const isSpy = ticker === 'SPY'
  const hasOos = result.results.some((r) => r.oos_total_pnl != null)

  // Drill-down state
  const [btResult, setBtResult] = useState<BacktestResponse | null>(null)
  const [btLoading, setBtLoading] = useState(false)
  const [btError, setBtError] = useState<string | null>(null)
  const [btLabel, setBtLabel] = useState('')
  const drillDownRef = useRef<HTMLDivElement>(null)

  const drillDown = (entry: OptimizeResultEntry) => {
    setBtLoading(true)
    setBtError(null)
    setBtResult(null)
    setBtLabel(`${ticker} @ ${interval} — ${SIGNAL_LABELS[entry.params.signal_type as string] || entry.params.signal_type}`)

    const p = entry.params
    const dEnd = new Date()
    const dStart = new Date()
    dStart.setDate(dEnd.getDate() - 180)
    const params: StockBacktestParams = {
      ticker,
      start_date: dStart.toISOString().slice(0, 10),
      end_date: dEnd.toISOString().slice(0, 10),
      signal_type: p.signal_type as string,
      ema_fast: p.ema_fast as number,
      ema_slow: p.ema_slow as number,
      bar_interval: interval,
      rsi_period: (p.rsi_period as number) || 0,
      rsi_ob: 70,
      rsi_os: 30,
      orb_minutes: (p.orb_minutes as number) || 15,
      atr_period: (p.atr_period as number) || 0,
      atr_stop_mult: (p.atr_stop_mult as number) || 2.0,
      afternoon_enabled: true,
      quantity: 2,
      stop_loss_percent: p.stop_loss_percent as number,
      profit_target_percent: p.profit_target_percent as number,
      trailing_stop_percent: p.trailing_stop_percent as number,
      max_hold_minutes: p.max_hold_minutes as number,
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

    setTimeout(() => drillDownRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100)

    runStockBacktest(params)
      .then((stockResult): BacktestResponse => ({
        summary: stockResult.summary,
        days: stockResult.days,
        trades: stockResult.trades.map((t) => ({
          ...t,
          scaled_out: false,
          scaled_out_price: null,
        })),
      }))
      .then((mapped) => {
        setBtResult(mapped)
        setTimeout(() => drillDownRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100)
      })
      .catch((err) => setBtError(err.response?.data?.detail || 'Backtest failed'))
      .finally(() => setBtLoading(false))
  }

  return (
    <div className="space-y-4">
      <div className="bg-surface rounded-lg p-4 flex items-center gap-6 text-sm flex-wrap">
        <span className="text-secondary">
          Tested <span className="text-heading font-semibold">{result.total_combinations_tested}</span> combinations
          in <span className="text-heading font-semibold">{result.elapsed_seconds.toFixed(1)}s</span>
        </span>
        <span className="text-secondary">
          Ranked by: <span className="text-purple-400 font-medium">{metricLabel}</span>
        </span>
        {result.train_start && result.test_start && (
          <span className="text-secondary">
            Walk-forward: train <span className="text-blue-400">{result.train_start}</span> to <span className="text-blue-400">{result.train_end}</span>
            {' | '}test <span className="text-orange-400">{result.test_start}</span> to <span className="text-orange-400">{result.test_end}</span>
          </span>
        )}
      </div>

      <div className="bg-surface rounded-lg p-6 overflow-x-auto">
        <h2 className="text-lg font-semibold mb-4">
          Top {result.results.length} Parameter Sets
          {!isSpy && <span className="text-secondary font-normal ml-2">for {ticker}</span>}
        </h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-secondary border-b border-subtle">
              <th className="text-left pb-3 pr-3">#</th>
              <th className="text-left pb-3 pr-3">Signal</th>
              <th className="text-right pb-3 pr-3">EMA</th>
              <th className="text-right pb-3 pr-3">SL%</th>
              <th className="text-right pb-3 pr-3">PT%</th>
              <th className="text-right pb-3 pr-3">Trail%</th>
              {isSpy && <th className="text-right pb-3 pr-3">SO%</th>}
              {isSpy && <th className="text-right pb-3 pr-3">Delta</th>}
              <th className="text-right pb-3 pr-3">Hold</th>
              <th className="text-right pb-3 pr-3">Score</th>
              <th className="text-right pb-3 pr-3">P&L</th>
              <th className="text-right pb-3 pr-3">Trades</th>
              <th className="text-right pb-3 pr-3">WR%</th>
              <th className="text-right pb-3 pr-3">PF</th>
              <th className="text-right pb-3 pr-3">MaxDD</th>
              {hasOos && <th className="text-right pb-3 pr-3 text-orange-400/70">OOS P&L</th>}
              {hasOos && <th className="text-right pb-3 pr-3 text-orange-400/70">OOS WR%</th>}
              {hasOos && <th className="text-right pb-3 pr-3 text-orange-400/70">OOS PF</th>}
              <th className="text-center pb-3"></th>
            </tr>
          </thead>
          <tbody>
            {result.results.map((r) => (
              <tr key={r.rank} className="border-b border-row hover:bg-hover">
                <td className="py-2.5 pr-3 text-muted font-medium">{r.rank}</td>
                <td className="py-2.5 pr-3 text-xs">
                  {SIGNAL_LABELS[(r.params.signal_type as string)] || (r.params.signal_type as string).replace('_', ' ')}
                </td>
                <td className="py-2.5 pr-3 text-right text-xs text-tertiary">
                  {r.params.ema_fast}/{r.params.ema_slow}
                </td>
                <td className="py-2.5 pr-3 text-right">{r.params.stop_loss_percent}%</td>
                <td className="py-2.5 pr-3 text-right">{r.params.profit_target_percent}%</td>
                <td className="py-2.5 pr-3 text-right">{r.params.trailing_stop_percent}%</td>
                {isSpy && <td className="py-2.5 pr-3 text-right text-secondary">{r.params.trailing_stop_after_scale_out_percent ?? 10}%</td>}
                {isSpy && <td className="py-2.5 pr-3 text-right text-tertiary">{r.params.delta_target}</td>}
                <td className="py-2.5 pr-3 text-right text-tertiary">{r.params.max_hold_minutes}m</td>
                <td className="py-2.5 pr-3 text-right text-purple-400 font-semibold">{r.score.toFixed(2)}</td>
                <td className={`py-2.5 pr-3 text-right font-semibold ${r.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {formatCurrency(r.total_pnl)}
                </td>
                <td className="py-2.5 pr-3 text-right text-tertiary">{r.total_trades}</td>
                <td className={`py-2.5 pr-3 text-right ${r.win_rate >= 50 ? 'text-green-400' : 'text-yellow-400'}`}>
                  {r.win_rate.toFixed(0)}%
                </td>
                <td className={`py-2.5 pr-3 text-right ${r.profit_factor >= 1 ? 'text-green-400' : 'text-red-400'}`}>
                  {r.profit_factor.toFixed(2)}
                </td>
                <td className="py-2.5 pr-3 text-right text-red-400">
                  {formatCurrency(r.max_drawdown)}
                </td>
                {hasOos && (
                  <td className={`py-2.5 pr-3 text-right font-semibold ${(r.oos_total_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {r.oos_total_pnl != null ? formatCurrency(r.oos_total_pnl) : '-'}
                  </td>
                )}
                {hasOos && (
                  <td className={`py-2.5 pr-3 text-right ${(r.oos_win_rate ?? 0) >= 50 ? 'text-green-400' : 'text-yellow-400'}`}>
                    {r.oos_win_rate != null ? `${r.oos_win_rate.toFixed(0)}%` : '-'}
                  </td>
                )}
                {hasOos && (
                  <td className={`py-2.5 pr-3 text-right ${(r.oos_profit_factor ?? 0) >= 1 ? 'text-green-400' : 'text-red-400'}`}>
                    {r.oos_profit_factor != null ? r.oos_profit_factor.toFixed(2) : '-'}
                  </td>
                )}
                <td className="py-2.5 text-center">
                  {onApply ? (
                    <button
                      onClick={() => onApply(r)}
                      className="px-3 py-1 rounded bg-blue-600/30 hover:bg-blue-600 text-blue-400 hover:text-white text-xs font-medium transition-colors"
                    >
                      Apply
                    </button>
                  ) : (
                    <button
                      onClick={() => drillDown(r)}
                      disabled={btLoading}
                      className="px-3 py-1 rounded bg-green-600/30 hover:bg-green-600 text-green-400 hover:text-white text-xs font-medium transition-colors disabled:opacity-50"
                    >
                      Drill Down
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Drill-down view */}
      <div ref={drillDownRef} />
      {btLoading && (
        <div className="flex items-center justify-center py-12 text-secondary">
          <svg className="animate-spin h-6 w-6 mr-3" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Running {ticker} backtest...
        </div>
      )}
      {btError && <div className="bg-surface rounded-lg p-4 text-red-400">{btError}</div>}
      {btResult && !btLoading && (
        <>
          <div className="bg-surface rounded-lg p-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold">{btLabel}</h2>
            <button
              onClick={() => { setBtResult(null); setBtError(null) }}
              className="text-xs text-secondary hover:text-heading"
            >
              Close
            </button>
          </div>
          <SummaryCards summary={btResult.summary} />
          <DailyPnLChart days={btResult.days} totalPnl={btResult.summary.total_pnl} />
          <ExitReasons reasons={btResult.summary.exit_reasons} />
          <TradeList trades={btResult.trades} />
        </>
      )}
    </div>
  )
}


// ── Backtest Result Components ───────────────────────────────────

function SignalBreakdownTable({ breakdown, onSelect }: { breakdown: Record<string, BacktestResponse>; onSelect: (signal: string) => void }) {
  const rows = Object.entries(breakdown)
    .map(([signal, r]) => ({ signal, s: r.summary }))
    .sort((a, b) => b.s.total_pnl - a.s.total_pnl)

  return (
    <div className="bg-surface rounded-lg border border-subtle overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-subtle text-secondary">
            <th className="text-left px-3 py-2 font-medium">Signal</th>
            <th className="text-right px-3 py-2 font-medium">P&L</th>
            <th className="text-right px-3 py-2 font-medium">Trades</th>
            <th className="text-right px-3 py-2 font-medium">Win%</th>
            <th className="text-right px-3 py-2 font-medium">PF</th>
            <th className="text-right px-3 py-2 font-medium">Avg Win</th>
            <th className="text-right px-3 py-2 font-medium">Avg Loss</th>
            <th className="text-right px-3 py-2 font-medium">Max DD</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ signal, s }) => (
            <tr key={signal} className="border-b border-subtle/50 hover:bg-hover cursor-pointer" onClick={() => onSelect(signal)}>
              <td className="px-3 py-1.5 font-medium text-blue-400 hover:text-blue-300">{SIGNAL_LABELS[signal] || signal}</td>
              <td className={`text-right px-3 py-1.5 ${s.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {formatCurrency(s.total_pnl)}
              </td>
              <td className="text-right px-3 py-1.5 text-primary">{s.total_trades}</td>
              <td className={`text-right px-3 py-1.5 ${s.win_rate >= 50 ? 'text-green-400' : 'text-yellow-400'}`}>
                {s.win_rate.toFixed(0)}%
              </td>
              <td className={`text-right px-3 py-1.5 ${s.profit_factor >= 1 ? 'text-green-400' : 'text-red-400'}`}>
                {s.profit_factor.toFixed(2)}
              </td>
              <td className="text-right px-3 py-1.5 text-green-400">{formatCurrency(s.avg_win)}</td>
              <td className="text-right px-3 py-1.5 text-red-400">{formatCurrency(s.avg_loss)}</td>
              <td className="text-right px-3 py-1.5 text-red-400">{formatCurrency(s.max_drawdown)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SummaryCards({ summary }: { summary: BacktestResponse['summary'] }) {
  const cards = [
    { label: 'Total P&L', value: formatCurrency(summary.total_pnl), color: summary.total_pnl >= 0 ? 'text-green-400' : 'text-red-400' },
    { label: 'Win Rate', value: `${summary.win_rate.toFixed(1)}%`, color: summary.win_rate >= 50 ? 'text-green-400' : 'text-yellow-400' },
    { label: 'Trades', value: `${summary.total_trades}`, color: 'text-blue-400', sub: `${summary.winning_trades}W / ${summary.losing_trades}L` },
    { label: 'Profit Factor', value: summary.profit_factor > 0 ? summary.profit_factor.toFixed(2) : '-', color: summary.profit_factor >= 1 ? 'text-green-400' : 'text-red-400' },
    { label: 'Max Drawdown', value: formatCurrency(summary.max_drawdown), color: 'text-red-400' },
    { label: 'Avg Win', value: formatCurrency(summary.avg_win), color: 'text-green-400' },
    { label: 'Avg Loss', value: formatCurrency(summary.avg_loss), color: 'text-red-400' },
    { label: 'Avg Hold', value: `${summary.avg_hold_minutes.toFixed(0)}m`, color: 'text-tertiary' },
  ]

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
      {cards.map((c) => (
        <div key={c.label} className="bg-surface rounded-lg p-3">
          <p className="text-xs text-secondary mb-1">{c.label}</p>
          <p className={`text-lg font-bold ${c.color}`}>{c.value}</p>
          {c.sub && <p className="text-xs text-muted">{c.sub}</p>}
        </div>
      ))}
    </div>
  )
}

function DailyPnLChart({ days, totalPnl }: { days: BacktestResponse['days']; totalPnl: number }) {
  const cc = useChartColors()
  const tradingDays = days.filter((d) => d.total_trades > 0 || d.pnl !== 0)
  if (tradingDays.length === 0) return null

  return (
    <div className="bg-surface rounded-lg p-6">
      <div className="flex items-baseline justify-between mb-1">
        <h2 className="text-lg font-semibold">Daily P&L</h2>
        <span className="text-xs text-secondary">{days.length} days</span>
      </div>
      <p className={`text-3xl font-bold mb-4 ${totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
        {formatCurrency(totalPnl)}
      </p>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={days}>
          <CartesianGrid strokeDasharray="3 3" stroke={cc.grid} />
          <XAxis dataKey="trade_date" tickFormatter={formatDateShort} stroke={cc.axis} tick={{ fontSize: 11 }} />
          <YAxis tickFormatter={(v: number) => formatCurrency(v)} stroke={cc.axis} tick={{ fontSize: 11 }} />
          <Tooltip
            formatter={(value: number) => [formatCurrency(value), 'P&L']}
            labelFormatter={formatDateShort}
            contentStyle={{ backgroundColor: cc.tooltipBg, border: `1px solid ${cc.tooltipBorder}` }}
          />
          <ReferenceLine y={0} stroke={cc.ref} strokeDasharray="3 3" />
          <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
            {days.map((d, i) => (
              <Cell key={i} fill={d.pnl >= 0 ? cc.green : cc.red} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function ExitReasons({ reasons }: { reasons: Record<string, number> }) {
  const entries = Object.entries(reasons).sort((a, b) => b[1] - a[1])
  if (entries.length === 0) return null

  return (
    <div className="bg-surface rounded-lg p-6">
      <h2 className="text-lg font-semibold mb-3">Exit Reasons</h2>
      <div className="flex flex-wrap gap-2">
        {entries.map(([reason, count]) => (
          <span
            key={reason}
            className={`px-3 py-1.5 rounded text-sm font-medium ${REASON_COLORS[reason] || 'bg-elevated text-tertiary'}`}
          >
            {reason.replace(/_/g, ' ')} ({count})
          </span>
        ))}
      </div>
    </div>
  )
}

function TradeList({ trades }: { trades: BacktestTrade[] }) {
  if (trades.length === 0) return null

  return (
    <div className="bg-surface rounded-lg p-6 overflow-x-auto">
      <h2 className="text-lg font-semibold mb-4">Trades ({trades.length})</h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-secondary border-b border-subtle">
            <th className="text-left pb-3 pr-4">Date / Time</th>
            <th className="text-left pb-3 pr-4">Dir</th>
            <th className="text-left pb-3 pr-4">Signal</th>
            <th className="text-right pb-3 pr-4">Strike</th>
            <th className="text-right pb-3 pr-4">Underlying</th>
            <th className="text-left pb-3 pr-4">Expiry</th>
            <th className="text-right pb-3 pr-4">DTE</th>
            <th className="text-right pb-3 pr-4">Delta</th>
            <th className="text-right pb-3 pr-4">Entry</th>
            <th className="text-right pb-3 pr-4">Exit</th>
            <th className="text-right pb-3 pr-4">Max</th>
            <th className="text-right pb-3 pr-4">P&L ($)</th>
            <th className="text-right pb-3 pr-4">P&L (%)</th>
            <th className="text-left pb-3 pr-4">Reason</th>
            <th className="text-right pb-3">Hold</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={i} className="border-b border-row hover:bg-hover">
              <td className="py-2 pr-4 text-xs text-secondary">{formatDateShort(t.trade_date)} {formatDt(t.entry_time)}</td>
              <td className={`py-2 pr-4 font-semibold ${t.direction === 'CALL' ? 'text-green-400' : 'text-red-400'}`}>
                {t.direction}
              </td>
              <td className="py-2 pr-4 text-xs text-secondary max-w-[200px] truncate" title={t.entry_reason || ''}>
                {t.entry_reason || '-'}
              </td>
              <td className="py-2 pr-4 text-right">${t.strike.toFixed(0)}</td>
              <td className="py-2 pr-4 text-right text-secondary">
                {t.underlying_price != null ? `$${t.underlying_price.toFixed(2)}` : '-'}
              </td>
              <td className="py-2 pr-4 text-xs text-secondary">
                {t.expiry_date ? formatDateShort(t.expiry_date) : '-'}
              </td>
              <td className="py-2 pr-4 text-right text-secondary">{t.dte}</td>
              <td className="py-2 pr-4 text-right text-secondary">
                {t.delta != null ? t.delta.toFixed(2) : '-'}
              </td>
              <td className="py-2 pr-4 text-right">${t.entry_price.toFixed(2)}</td>
              <td className="py-2 pr-4 text-right">{t.exit_price != null ? `$${t.exit_price.toFixed(2)}` : '-'}</td>
              <td className="py-2 pr-4 text-right text-secondary">${t.highest_price_seen.toFixed(2)}</td>
              <td className={`py-2 pr-4 text-right font-semibold ${(t.pnl_dollars ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {t.pnl_dollars != null ? formatCurrency(t.pnl_dollars) : '-'}
              </td>
              <td className={`py-2 pr-4 text-right ${(t.pnl_percent ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {t.pnl_percent != null ? `${t.pnl_percent > 0 ? '+' : ''}${t.pnl_percent.toFixed(1)}%` : '-'}
              </td>
              <td className="py-2 pr-4 text-xs" title={t.exit_detail || ''}>{t.exit_reason?.replace(/_/g, ' ') || '-'}</td>
              <td className="py-2 text-right text-xs text-secondary">
                {t.hold_minutes != null ? `${t.hold_minutes.toFixed(0)}m` : '-'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
