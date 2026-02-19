import { useState, useEffect, useRef } from 'react'
import {
  BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import {
  runBacktest, runOptimization,
  type BacktestParams, type BacktestResponse, type BacktestTrade,
  type OptimizeParams, type OptimizeResponse, type OptimizeResultEntry,
  type SignalType,
} from '../api/backtest'
import {
  getAvailableTickers, runStockOptimization, runStockBacktest,
  type TickerInfo, type StockBacktestParams,
} from '../api/stockBacktest'
import { formatCurrency } from '../utils/format'
import { useChartColors } from '../hooks/useChartColors'
import TopSetups from './TopSetups'

function defaultParams(): BacktestParams {
  const end = new Date()
  const start = new Date()
  start.setDate(end.getDate() - 30)
  return {
    start_date: start.toISOString().slice(0, 10),
    end_date: end.toISOString().slice(0, 10),
    signal_type: 'ema_cross',
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
    delta_target: 0.4,
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
    max_daily_loss: 500,
    max_consecutive_losses: 3,
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
  { value: 'composite', label: 'Composite (PF x \u221Atrades)' },
  { value: 'total_pnl', label: 'Total P&L' },
  { value: 'profit_factor', label: 'Profit Factor' },
  { value: 'win_rate', label: 'Win Rate' },
  { value: 'risk_adjusted', label: 'Risk Adjusted (PnL / DD)' },
] as const

export default function Backtest() {
  const [activeTab, setActiveTab] = useState<'setups' | 'backtest' | 'optimize'>('setups')
  const [params, setParams] = useState<BacktestParams>(defaultParams)

  // Backtest state
  const [backtestTicker, setBacktestTicker] = useState('SPY')
  const [result, setResult] = useState<BacktestResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Optimizer state
  const [optimizeResult, setOptimizeResult] = useState<OptimizeResponse | null>(null)
  const [optimizing, setOptimizing] = useState(false)
  const [optimizeError, setOptimizeError] = useState<string | null>(null)
  const [numIterations, setNumIterations] = useState(200)
  const [targetMetric, setTargetMetric] = useState<OptimizeParams['target_metric']>('composite')
  const [optimizeTicker, setOptimizeTicker] = useState('SPY')
  const [optimizeInterval, setOptimizeInterval] = useState('5m')
  const [availableTickers, setAvailableTickers] = useState<TickerInfo[]>([])

  useEffect(() => {
    getAvailableTickers().then(setAvailableTickers).catch(() => {})
  }, [])

  const set = <K extends keyof BacktestParams>(k: K, v: BacktestParams[K]) =>
    setParams((p) => ({ ...p, [k]: v }))

  const isSpy = backtestTicker === 'SPY'

  const run = () => {
    setLoading(true)
    setError(null)

    const promise: Promise<BacktestResponse> = isSpy
      ? runBacktest(params)
      : runStockBacktest({
          ticker: backtestTicker,
          start_date: params.start_date,
          end_date: params.end_date,
          signal_type: params.signal_type,
          ema_fast: params.ema_fast,
          ema_slow: params.ema_slow,
          bar_interval: params.bar_interval,
          rsi_period: params.rsi_period,
          rsi_ob: params.rsi_ob,
          rsi_os: params.rsi_os,
          orb_minutes: params.orb_minutes,
          atr_period: params.atr_period,
          atr_stop_mult: params.atr_stop_mult,
          afternoon_enabled: params.afternoon_enabled,
          quantity: params.quantity,
          stop_loss_percent: params.stop_loss_percent,
          profit_target_percent: params.profit_target_percent,
          trailing_stop_percent: params.trailing_stop_percent,
          max_hold_minutes: params.max_hold_minutes,
          min_confluence: params.min_confluence,
          vol_threshold: params.vol_threshold,
          orb_body_min_pct: 0,
          orb_vwap_filter: false,
          orb_gap_fade_filter: false,
          orb_stop_mult: 1.0,
          orb_target_mult: 1.5,
          max_daily_trades: params.max_daily_trades,
          max_daily_loss: params.max_daily_loss,
          max_consecutive_losses: params.max_consecutive_losses,
        }).then((stockResult): BacktestResponse => ({
          summary: stockResult.summary,
          days: stockResult.days,
          trades: stockResult.trades.map((t) => ({
            ...t,
            scaled_out: false,
            scaled_out_price: null,
          })),
        }))

    promise
      .then(setResult)
      .catch((err) => setError(err.response?.data?.detail || 'Backtest failed'))
      .finally(() => setLoading(false))
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
                <select value={backtestTicker}
                  onChange={(e) => {
                    const t = e.target.value
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
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading">
                  <option value="SPY">SPY</option>
                  {availableTickers.map((t) => (
                    <option key={t.ticker} value={t.ticker}>{t.ticker}</option>
                  ))}
                </select>
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
                  onChange={(e) => set('signal_type', e.target.value as SignalType)}
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading">
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

              <button
                onClick={run}
                disabled={loading}
                className="ml-auto px-6 py-2 rounded bg-blue-600 hover:bg-blue-500 text-white font-medium text-sm disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? 'Running...' : `Run ${backtestTicker} Backtest`}
              </button>
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

          {result && !loading && (
            <>
              <SummaryCards summary={result.summary} />
              <DailyPnLChart days={result.days} totalPnl={result.summary.total_pnl} />
              <ExitReasons reasons={result.summary.exit_reasons} />
              <TradeList trades={result.trades} />
            </>
          )}
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
                <select value={optimizeTicker}
                  onChange={(e) => {
                    const t = e.target.value
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
                  className="w-full bg-elevated rounded px-2 py-1.5 text-heading">
                  <option value="SPY">SPY</option>
                  {availableTickers.map((t) => (
                    <option key={t.ticker} value={t.ticker}>{t.ticker}</option>
                  ))}
                </select>
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
    const params: StockBacktestParams = {
      ticker,
      start_date: '2025-01-01',
      end_date: '2026-12-31',
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
      max_daily_loss: 500,
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
      <div className="bg-surface rounded-lg p-4 flex items-center gap-6 text-sm">
        <span className="text-secondary">
          Tested <span className="text-heading font-semibold">{result.total_combinations_tested}</span> combinations
          in <span className="text-heading font-semibold">{result.elapsed_seconds.toFixed(1)}s</span>
        </span>
        <span className="text-secondary">
          Ranked by: <span className="text-purple-400 font-medium">{metricLabel}</span>
        </span>
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
              <td className="py-2 pr-4 text-xs">{t.exit_reason?.replace(/_/g, ' ') || '-'}</td>
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
