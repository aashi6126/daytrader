import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import {
  getSavedResults,
  clearSavedResults,
  startBatchOptimize,
  getBatchOptimizeStatus,
  getMarketCapTiers,
  getAvailableTickers,
  type StockOptimizeResultEntry, type StockBacktestResponse,
  type StockBacktestParams,
  type EnabledStrategyEntry,
  type FavoriteStrategy,
  type BatchOptimizeStatus,
  type MarketCapTier,
  type TickerInfo,
  runStockBacktest,
  getStrategyStatus,
  enableStrategy,
  disableStrategy,
  getFavorites,
  saveFavorite,
  deleteFavorite,
} from '../api/stockBacktest'
import { formatCurrency } from '../utils/format'
import { useChartColors } from '../hooks/useChartColors'

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

const SIGNAL_DESCRIPTIONS: Record<string, { summary: string; entry: string; exit: string }> = {
  ema_cross: {
    summary: 'Trades when a fast EMA crosses above (CALL) or below (PUT) a slow EMA, signaling momentum shifts.',
    entry: 'Buy CALL when fast EMA crosses above slow EMA. Buy PUT when fast EMA crosses below slow EMA.',
    exit: 'Stop loss, trailing stop, or profit target on the option price.',
  },
  vwap_cross: {
    summary: 'Trades when price crosses above or below the Volume Weighted Average Price, a key institutional level.',
    entry: 'Buy CALL when price crosses above VWAP. Buy PUT when price crosses below VWAP.',
    exit: 'Stop loss, trailing stop, or profit target on the option price.',
  },
  ema_vwap: {
    summary: 'Combines EMA crossover with VWAP confirmation. Both must agree on direction for higher conviction.',
    entry: 'Buy CALL when fast EMA > slow EMA AND price > VWAP. Buy PUT when fast EMA < slow EMA AND price < VWAP.',
    exit: 'Stop loss, trailing stop, or profit target on the option price.',
  },
  orb: {
    summary: 'Opening Range Breakout. Waits for price to break above/below the high/low of the first N minutes.',
    entry: 'Buy CALL on break above ORB high. Buy PUT on break below ORB low.',
    exit: 'ORB-based stops using range multiples, or time-based exit.',
  },
  orb_direction: {
    summary: 'Enhanced ORB with directional filters: candle body %, VWAP alignment, and gap fade detection.',
    entry: 'Like ORB, but only enters if the opening candle body is strong, direction aligns with VWAP, and gap fades are filtered.',
    exit: 'ORB range-based stop (stop mult) and target (target mult), plus time stop.',
  },
  vwap_rsi: {
    summary: 'Uses VWAP for direction and RSI for timing. Enters when momentum aligns with the dominant trend.',
    entry: 'Buy CALL when price > VWAP and RSI crosses above oversold. Buy PUT when price < VWAP and RSI crosses below overbought.',
    exit: 'Stop loss, trailing stop, or profit target on the option price.',
  },
  vwap_reclaim: {
    summary: 'Enters when price reclaims VWAP with a strong candle body (>=$0.30), indicating conviction.',
    entry: 'Buy CALL when price crosses above VWAP with a bullish body >= $0.30. Buy PUT on bearish VWAP cross with strong body.',
    exit: 'Stop loss, trailing stop, or profit target on the option price.',
  },
  bb_squeeze: {
    summary: 'Bollinger Band squeeze breakout. Detects low-volatility compression then trades the directional breakout.',
    entry: 'Buy CALL when price breaks above upper BB after a squeeze. Buy PUT on break below lower BB after squeeze.',
    exit: 'Stop loss, trailing stop, or profit target on the option price.',
  },
  rsi_reversal: {
    summary: 'Mean reversion using RSI extremes. Enters when RSI reverses from overbought/oversold territory.',
    entry: 'Buy CALL when RSI crosses back above oversold (30). Buy PUT when RSI crosses back below overbought (70).',
    exit: 'Stop loss, trailing stop, or profit target on the option price.',
  },
  confluence: {
    summary: 'Multi-indicator scoring system. Scores 6 factors (VWAP, EMA, RSI, MACD, Volume, Candle) and requires minimum agreement.',
    entry: 'Buy CALL when confluence score >= threshold (all bullish). Buy PUT when score <= -threshold (all bearish). Uses EMA, VWAP, RSI, MACD, volume surge, and candle body direction.',
    exit: 'Stop loss, trailing stop, or profit target on the option price.',
  },
}

const REASON_COLORS: Record<string, string> = {
  STOP_LOSS: 'bg-red-500/20 text-red-400',
  TRAILING_STOP: 'bg-orange-500/20 text-orange-400',
  PROFIT_TARGET: 'bg-green-500/20 text-green-400',
  MAX_HOLD_TIME: 'bg-yellow-500/20 text-yellow-400',
  TIME_BASED: 'bg-blue-500/20 text-blue-400',
  ORB_TIME_STOP: 'bg-purple-500/20 text-purple-400',
}

function formatDateShort(dateStr: string) {
  const d = new Date(dateStr + 'T12:00:00')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function formatDt(iso: string) {
  const d = new Date(iso)
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' })
}


export default function TopSetups() {
  const navigate = useNavigate()
  const [savedResults, setSavedResults] = useState<StockOptimizeResultEntry[]>([])
  const [loadingResults, setLoadingResults] = useState(true)

  // Batch optimize state
  const [batchStatus, setBatchStatus] = useState<BatchOptimizeStatus | null>(null)
  const [batchIterations, setBatchIterations] = useState(200)
  const [batchMetric, setBatchMetric] = useState('pro')
  const [minTrades, setMinTrades] = useState(10)
  const [marketCapTier, setMarketCapTier] = useState('all')
  const [tierOptions, setTierOptions] = useState<MarketCapTier[]>([])
  const [availableTickers, setAvailableTickers] = useState<TickerInfo[]>([])
  const [selectedTickers, setSelectedTickers] = useState<string[]>([])
  const pollRef = useRef<ReturnType<typeof setInterval>>()

  // Drill-down backtest state
  const [btResult, setBtResult] = useState<StockBacktestResponse | null>(null)
  const [btLoading, setBtLoading] = useState(false)
  const [btError, setBtError] = useState<string | null>(null)
  const [btLabel, setBtLabel] = useState('')
  const [btSignalType, setBtSignalType] = useState('')
  const [btParams, setBtParams] = useState<Record<string, number | string | boolean>>({})
  const [showDrillDown, setShowDrillDown] = useState(false)
  const drillDownRef = useRef<HTMLDivElement>(null)

  // Enabled strategies state (multi-strategy)
  const [enabledStrategies, setEnabledStrategies] = useState<EnabledStrategyEntry[]>([])
  const [enabling, setEnabling] = useState(false)

  // Favorites state
  const [favorites, setFavorites] = useState<FavoriteStrategy[]>([])

  const loadResults = useCallback(() => {
    setLoadingResults(true)
    getSavedResults(minTrades, 50)
      .then(setSavedResults)
      .catch(() => {})
      .finally(() => setLoadingResults(false))
  }, [minTrades])

  useEffect(() => {
    loadResults()
    getStrategyStatus()
      .then((r) => setEnabledStrategies(r.strategies))
      .catch(() => {})
    getFavorites()
      .then(setFavorites)
      .catch(() => {})
    getMarketCapTiers()
      .then(setTierOptions)
      .catch(() => {})
    getAvailableTickers()
      .then(setAvailableTickers)
      .catch(() => {})

    // Check if a batch job is already running
    getBatchOptimizeStatus()
      .then((s) => {
        if (s.status === 'running') {
          setBatchStatus(s)
          startPolling()
        }
      })
      .catch(() => {})

    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  const startPolling = () => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const s = await getBatchOptimizeStatus()
        setBatchStatus(s)
        if (s.status !== 'running') {
          clearInterval(pollRef.current)
          pollRef.current = undefined
          loadResults()
        } else {
          // Load partial results while optimizer is still running
          loadResults()
        }
      } catch {
        clearInterval(pollRef.current)
        pollRef.current = undefined
      }
    }, 2000)
  }

  const handleStartBatch = async () => {
    try {
      const s = await startBatchOptimize({
        iterations: batchIterations,
        metric: batchMetric,
        min_trades: minTrades,
        market_cap_tier: marketCapTier,
        ...(selectedTickers.length > 0 ? { tickers: selectedTickers } : {}),
      })
      setBatchStatus(s)
      startPolling()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Failed to start'
      setBatchStatus({ status: 'failed', progress: '', elapsed_seconds: 0, results_count: 0, error: msg })
    }
  }

  const isFavorited = (entry: StockOptimizeResultEntry) => {
    return favorites.some(
      (f) =>
        f.ticker === entry.ticker &&
        f.params.signal_type === entry.params.signal_type &&
        f.params.bar_interval === entry.timeframe
    )
  }

  const getFavoriteId = (entry: StockOptimizeResultEntry) => {
    const fav = favorites.find(
      (f) =>
        f.ticker === entry.ticker &&
        f.params.signal_type === entry.params.signal_type &&
        f.params.bar_interval === entry.timeframe
    )
    return fav?.id
  }

  const handleToggleFavorite = async (entry: StockOptimizeResultEntry) => {
    const existingId = getFavoriteId(entry)
    if (existingId) {
      try {
        await deleteFavorite(existingId)
        setFavorites((prev) => prev.filter((f) => f.id !== existingId))
      } catch (err) {
        console.error('Failed to remove favorite:', err)
      }
    } else {
      try {
        const signalLabel = SIGNAL_LABELS[entry.params.signal_type as string] || entry.params.signal_type
        const fav = await saveFavorite({
          ticker: entry.ticker,
          strategy_name: `${entry.ticker} ${signalLabel} ${entry.timeframe}`,
          params: { ...entry.params, bar_interval: entry.timeframe },
          summary: {
            total_pnl: entry.total_pnl,
            total_trades: entry.total_trades,
            win_rate: entry.win_rate,
            profit_factor: entry.profit_factor,
            max_drawdown: entry.max_drawdown,
            avg_hold_minutes: entry.avg_hold_minutes,
          },
        })
        setFavorites((prev) => [...prev, fav])
      } catch (err) {
        console.error('Failed to save favorite:', err)
      }
    }
  }

  const isStrategyEnabled = (entry: StockOptimizeResultEntry) => {
    return enabledStrategies.some(
      (s) =>
        s.ticker === entry.ticker &&
        s.timeframe === entry.timeframe &&
        s.signal_type === (entry.params.signal_type as string)
    )
  }

  const handleToggleStrategy = async (entry: StockOptimizeResultEntry) => {
    setEnabling(true)
    try {
      if (isStrategyEnabled(entry)) {
        const resp = await disableStrategy({
          ticker: entry.ticker,
          timeframe: entry.timeframe,
          signal_type: entry.params.signal_type as string,
        })
        setEnabledStrategies(resp.strategies)
      } else {
        const resp = await enableStrategy({
          ticker: entry.ticker,
          timeframe: entry.timeframe,
          signal_type: entry.params.signal_type as string,
          params: entry.params,
        })
        setEnabledStrategies(resp.strategies)
      }
    } catch (err) {
      console.error('Strategy toggle failed:', err)
    } finally {
      setEnabling(false)
    }
  }

  const drillDown = (entry: StockOptimizeResultEntry) => {
    setBtLoading(true)
    setBtError(null)
    setBtResult(null)
    setShowDrillDown(true)
    setBtLabel(`${entry.ticker} @ ${entry.timeframe} — ${SIGNAL_LABELS[entry.params.signal_type as string] || entry.params.signal_type}`)
    setBtSignalType(entry.params.signal_type as string)
    setBtParams(entry.params)

    const p = entry.params
    const params: StockBacktestParams = {
      ticker: entry.ticker,
      start_date: '2025-01-01',
      end_date: '2026-12-31',
      signal_type: p.signal_type as string,
      ema_fast: p.ema_fast as number,
      ema_slow: p.ema_slow as number,
      bar_interval: entry.timeframe,
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
      entry_confirm_minutes: (p.entry_confirm_minutes as number) || 0,
    }

    // Scroll to drill-down section immediately
    setTimeout(() => drillDownRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100)

    runStockBacktest(params)
      .then((r) => {
        setBtResult(r)
        setTimeout(() => drillDownRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100)
      })
      .catch((err) => setBtError(err.response?.data?.detail || 'Backtest failed'))
      .finally(() => setBtLoading(false))
  }

  const isRunning = batchStatus?.status === 'running'

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      {/* Batch optimize controls */}
      <div className="bg-surface rounded-lg p-4">
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <label className="text-xs text-secondary">Iterations</label>
            <input
              type="number" min={10} max={2000} step={50} value={batchIterations}
              onChange={(e) => setBatchIterations(+e.target.value)}
              disabled={isRunning}
              className="w-20 bg-elevated rounded px-2 py-1 text-sm text-heading disabled:opacity-50"
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-secondary">Metric</label>
            <select
              value={batchMetric}
              onChange={(e) => setBatchMetric(e.target.value)}
              disabled={isRunning}
              className="bg-elevated rounded px-2 py-1 text-sm text-heading disabled:opacity-50"
            >
              <option value="pro">Pro</option>
              <option value="risk_adjusted">Risk Adjusted</option>
              <option value="total_pnl">Total P&L</option>
              <option value="profit_factor">Profit Factor</option>
              <option value="composite">Composite</option>
              <option value="sharpe">Sharpe</option>
              <option value="win_rate">Win Rate</option>
            </select>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-secondary">Market Cap</label>
            <select
              value={marketCapTier}
              onChange={(e) => setMarketCapTier(e.target.value)}
              disabled={isRunning}
              className="bg-elevated rounded px-2 py-1 text-sm text-heading disabled:opacity-50"
            >
              {tierOptions.length > 0 ? tierOptions.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label} ({t.count})
                </option>
              )) : (
                <>
                  <option value="all">All</option>
                  <option value="mega">Mega Cap</option>
                  <option value="large">Large Cap</option>
                  <option value="mid">Mid Cap</option>
                  <option value="small">Small Cap</option>
                  <option value="etf">ETFs</option>
                </>
              )}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-secondary">Tickers</label>
            <div className="relative">
              <select
                value=""
                onChange={(e) => {
                  const val = e.target.value
                  if (val && !selectedTickers.includes(val)) {
                    setSelectedTickers((prev) => [...prev, val])
                  }
                }}
                disabled={isRunning}
                className="bg-elevated rounded px-2 py-1 text-sm text-heading disabled:opacity-50"
              >
                <option value="">{selectedTickers.length === 0 ? 'All Tickers' : 'Add ticker...'}</option>
                {availableTickers
                  .filter((t) => !selectedTickers.includes(t.ticker))
                  .map((t) => (
                    <option key={t.ticker} value={t.ticker}>{t.ticker}</option>
                  ))}
              </select>
            </div>
            {selectedTickers.length > 0 && (
              <div className="flex items-center gap-1 flex-wrap">
                {selectedTickers.map((t) => (
                  <span key={t} className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-green-600/20 text-green-400 text-xs font-medium">
                    {t}
                    <button
                      onClick={() => setSelectedTickers((prev) => prev.filter((x) => x !== t))}
                      disabled={isRunning}
                      className="hover:text-white text-green-400/60 disabled:opacity-50"
                    >
                      x
                    </button>
                  </span>
                ))}
                <button
                  onClick={() => setSelectedTickers([])}
                  disabled={isRunning}
                  className="text-xs text-secondary hover:text-heading disabled:opacity-50"
                >
                  Clear
                </button>
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-secondary">Min Trades</label>
            <input
              type="number" min={1} max={200} value={minTrades}
              onChange={(e) => { setMinTrades(+e.target.value); }}
              disabled={isRunning}
              className="w-16 bg-elevated rounded px-2 py-1 text-sm text-heading disabled:opacity-50"
            />
            <button
              onClick={loadResults}
              disabled={isRunning || loadingResults}
              className="px-2 py-1 rounded bg-elevated text-xs text-secondary hover:text-heading transition-colors disabled:opacity-50"
            >
              Apply
            </button>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={() => {
                if (!confirm('Clear all saved optimization results?')) return
                clearSavedResults().then(() => setSavedResults([])).catch(() => {})
              }}
              disabled={isRunning || savedResults.length === 0}
              className="px-3 py-1.5 rounded bg-red-600/20 hover:bg-red-600/40 text-red-400 text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              Clear Results
            </button>
            <button
              onClick={handleStartBatch}
              disabled={isRunning}
              className="px-4 py-1.5 rounded bg-green-600 hover:bg-green-500 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {isRunning ? 'Optimizing...' : 'Run Batch Optimize'}
            </button>
          </div>
        </div>

        {/* Progress indicator */}
        {batchStatus && batchStatus.status === 'running' && (
          <div className="mt-3 flex items-center gap-3">
            <svg className="animate-spin h-4 w-4 text-green-400" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <span className="text-sm text-secondary">
              {batchStatus.progress} &middot; {batchStatus.elapsed_seconds.toFixed(0)}s elapsed
            </span>
          </div>
        )}
        {batchStatus && batchStatus.status === 'completed' && (
          <div className="mt-3 text-sm text-green-400">
            Completed: {batchStatus.results_count} setups found in {batchStatus.elapsed_seconds.toFixed(0)}s
          </div>
        )}
        {batchStatus && batchStatus.status === 'failed' && (
          <div className="mt-3 text-sm text-red-400">
            Failed: {batchStatus.error}
          </div>
        )}
      </div>

      {loadingResults ? (
        <Spinner text="Loading saved results..." />
      ) : savedResults.length === 0 ? (
        <div className="bg-surface rounded-lg p-8 text-center text-secondary">
          No saved results found. Click "Run Batch Optimize" to find top setups across all tickers.
        </div>
      ) : (
        <ResultsTable results={savedResults} onDrillDown={drillDown} loading={btLoading}
          onToggleStrategy={handleToggleStrategy} isStrategyEnabled={isStrategyEnabled} enabling={enabling}
          onToggleFavorite={handleToggleFavorite} isFavorited={isFavorited}
          onEditTest={(entry) => navigate('/backtest', { state: { fromSetup: entry } })} />
      )}

      {/* Drill-down view */}
      <div ref={drillDownRef} />
      {showDrillDown && (
        <>
          {btLoading && <Spinner text="Running backtest..." />}
          {btError && <div className="bg-surface rounded-lg p-4 text-red-400">{btError}</div>}
          {btResult && !btLoading && (
            <>
              <div className="bg-surface rounded-lg p-4">
                <div className="flex items-center justify-between mb-2">
                  <h2 className="text-lg font-semibold">{btLabel}</h2>
                  <button
                    onClick={() => { setShowDrillDown(false); setBtResult(null) }}
                    className="text-xs text-secondary hover:text-heading"
                  >
                    Close
                  </button>
                </div>
                {SIGNAL_DESCRIPTIONS[btSignalType] && (
                  <div className="border-t border-subtle pt-3 mt-1 space-y-1.5">
                    <p className="text-sm text-secondary">{SIGNAL_DESCRIPTIONS[btSignalType].summary}</p>
                    <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
                      <span className="text-green-400/80"><span className="text-secondary">Entry:</span> {SIGNAL_DESCRIPTIONS[btSignalType].entry}</span>
                      <span className="text-orange-400/80"><span className="text-secondary">Exit:</span> {SIGNAL_DESCRIPTIONS[btSignalType].exit}</span>
                    </div>
                    <div className="flex flex-wrap gap-2 mt-2">
                      {btParams.stop_loss_percent != null && <span className="px-2 py-0.5 rounded bg-red-500/10 text-red-400 text-xs">SL {btParams.stop_loss_percent}%</span>}
                      {btParams.profit_target_percent != null && <span className="px-2 py-0.5 rounded bg-green-500/10 text-green-400 text-xs">PT {btParams.profit_target_percent}%</span>}
                      {btParams.trailing_stop_percent != null && <span className="px-2 py-0.5 rounded bg-orange-500/10 text-orange-400 text-xs">Trail {btParams.trailing_stop_percent}%</span>}
                      {btParams.max_hold_minutes != null && <span className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 text-xs">Hold {btParams.max_hold_minutes}m</span>}
                      {btParams.ema_fast != null && btSignalType.includes('ema') && <span className="px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 text-xs">EMA {btParams.ema_fast}/{btParams.ema_slow}</span>}
                      {btParams.rsi_period != null && Number(btParams.rsi_period) > 0 && <span className="px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-400 text-xs">RSI {btParams.rsi_period}</span>}
                      {btParams.orb_minutes != null && btSignalType.includes('orb') && <span className="px-2 py-0.5 rounded bg-yellow-500/10 text-yellow-400 text-xs">ORB {btParams.orb_minutes}m</span>}
                      {btParams.min_confluence != null && btSignalType === 'confluence' && <span className="px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 text-xs">Min Score {btParams.min_confluence}/6</span>}
                    </div>
                  </div>
                )}
              </div>
              <SummaryCards summary={btResult.summary} quantity={Number(btParams.quantity) || 2} />
              <DailyPnLChart days={btResult.days} totalPnl={btResult.summary.total_pnl} />
              <ExitReasons reasons={btResult.summary.exit_reasons} />
              <TradeList trades={btResult.trades} />
            </>
          )}
        </>
      )}
    </div>
  )
}


// ── Results table ─────────────────────────────────────────────────

type SortKey = 'total_pnl' | 'total_trades' | 'win_rate' | 'profit_factor' | 'max_drawdown' | 'avg_hold_minutes' | 'score' | 'oos_total_pnl' | 'oos_profit_factor' | 'mc_win_pct'
type SortDir = 'asc' | 'desc'

const SORT_COLUMNS: { key: SortKey; label: string; align: 'left' | 'right' }[] = [
  { key: 'total_pnl', label: 'IS P&L', align: 'right' },
  { key: 'total_trades', label: 'Trades', align: 'right' },
  { key: 'win_rate', label: 'WR%', align: 'right' },
  { key: 'profit_factor', label: 'PF', align: 'right' },
  { key: 'max_drawdown', label: 'MaxDD', align: 'right' },
  { key: 'score', label: 'IS Score', align: 'right' },
  { key: 'oos_total_pnl', label: 'OOS P&L', align: 'right' },
  { key: 'oos_profit_factor', label: 'OOS PF', align: 'right' },
  { key: 'mc_win_pct', label: 'MC Conf', align: 'right' },
]

function SortHeader({ label, sortKey, currentKey, dir, onSort, align }: {
  label: string; sortKey: SortKey; currentKey: SortKey; dir: SortDir
  onSort: (k: SortKey) => void; align: 'left' | 'right'
}) {
  const active = currentKey === sortKey
  return (
    <th
      className={`pb-3 pr-3 cursor-pointer select-none hover:text-primary transition-colors ${align === 'right' ? 'text-right' : 'text-left'} ${active ? 'text-green-400' : ''}`}
      onClick={() => onSort(sortKey)}
    >
      {label}{active ? (dir === 'desc' ? ' \u25BC' : ' \u25B2') : ''}
    </th>
  )
}

function ResultsTable({
  results,
  onDrillDown,
  loading,
  onToggleStrategy,
  isStrategyEnabled,
  enabling,
  onToggleFavorite,
  isFavorited,
  onEditTest,
}: {
  results: StockOptimizeResultEntry[]
  onDrillDown: (entry: StockOptimizeResultEntry) => void
  loading: boolean
  onToggleStrategy: (entry: StockOptimizeResultEntry) => void
  isStrategyEnabled: (entry: StockOptimizeResultEntry) => boolean
  enabling: boolean
  onToggleFavorite: (entry: StockOptimizeResultEntry) => void
  isFavorited: (entry: StockOptimizeResultEntry) => boolean
  onEditTest: (entry: StockOptimizeResultEntry) => void
}) {
  const [sortKey, setSortKey] = useState<SortKey>('score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [filterTicker, setFilterTicker] = useState<string | null>(null)
  const [enabledOnly, setEnabledOnly] = useState(false)

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const filtered = useMemo(() => {
    let list = results
    if (filterTicker) list = list.filter((r) => r.ticker === filterTicker)
    if (enabledOnly) list = list.filter((r) => isStrategyEnabled(r))
    return list
  }, [results, filterTicker, enabledOnly])

  const [rowLimit, setRowLimit] = useState(100)

  // Reset row limit when filter changes
  useEffect(() => { setRowLimit(100) }, [filterTicker, enabledOnly])

  const sorted = useMemo(() => {
    const copy = [...filtered]
    copy.sort((a, b) => {
      const av = (a[sortKey] as number | null | undefined) ?? -Infinity
      const bv = (b[sortKey] as number | null | undefined) ?? -Infinity
      return sortDir === 'desc' ? bv - av : av - bv
    })
    return copy
  }, [filtered, sortKey, sortDir])

  const visibleRows = sorted.slice(0, rowLimit)

  // Group best per ticker for the summary (by max PnL)
  const bestPerTicker = new Map<string, StockOptimizeResultEntry>()
  for (const r of results) {
    const existing = bestPerTicker.get(r.ticker)
    if (!existing || r.total_pnl > existing.total_pnl) {
      bestPerTicker.set(r.ticker, r)
    }
  }
  const tickerSummary = Array.from(bestPerTicker.values()).sort((a, b) => b.total_pnl - a.total_pnl)
  const [showAllTickers, setShowAllTickers] = useState(false)
  const visibleTickers = showAllTickers ? tickerSummary : tickerSummary.slice(0, 21)

  return (
    <div className="space-y-4">
      {/* Ticker cards summary */}
      {tickerSummary.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-2">
          {visibleTickers.map((r) => {
            const isActive = filterTicker === r.ticker
            return (
              <div key={r.ticker + r.timeframe}
                className={`rounded-lg p-3 cursor-pointer transition-colors ${
                  isActive
                    ? 'bg-green-900/40 ring-1 ring-green-500/50'
                    : r.ticker === 'SPY'
                      ? 'bg-blue-900/40 ring-1 ring-blue-500/30 hover:bg-hover'
                      : 'bg-surface hover:bg-hover'
                }`}
                onClick={() => setFilterTicker(isActive ? null : r.ticker)}>
                <div className="flex items-center justify-between mb-1">
                  <span className="font-bold text-sm text-heading">{r.ticker}</span>
                  <span className="text-xs text-tertiary font-medium">{r.timeframe}</span>
                </div>
                <p className={`text-sm font-semibold ${r.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {formatCurrency(r.total_pnl)}
                </p>
                {r.oos_total_pnl != null && (
                  <p className={`text-xs font-medium ${r.oos_total_pnl >= 0 ? 'text-green-400/70' : 'text-red-400/70'}`}>
                    OOS: {formatCurrency(r.oos_total_pnl)}
                  </p>
                )}
                <p className="text-xs text-tertiary">
                  {SIGNAL_LABELS[r.params.signal_type as string] || r.params.signal_type} &middot; {r.win_rate.toFixed(0)}% WR
                </p>
              </div>
            )
          })}
        </div>
      )}
      {tickerSummary.length > 21 && (
        <button
          onClick={() => setShowAllTickers((v) => !v)}
          className="w-full text-center text-xs text-secondary hover:text-heading py-1.5 bg-surface rounded-lg transition-colors"
        >
          {showAllTickers ? `Show Top 21` : `Show All ${tickerSummary.length} Tickers`}
        </button>
      )}

      {/* Full results table */}
      <div className="bg-surface rounded-lg p-6 overflow-x-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">
            {filterTicker ? `${filterTicker} Results (${sorted.length})` : `All Results (${sorted.length})`}
          </h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setEnabledOnly((v) => !v)}
              className={`text-xs px-2 py-1 rounded font-medium transition-colors ${
                enabledOnly
                  ? 'bg-yellow-600/30 text-yellow-300 ring-1 ring-yellow-500/50'
                  : 'bg-elevated text-tertiary hover:bg-elevated ring-1 ring-default'
              }`}
            >
              Enabled Only
            </button>
            {filterTicker && (
              <button
                onClick={() => setFilterTicker(null)}
                className="text-xs text-secondary hover:text-heading px-2 py-1 rounded bg-elevated hover:bg-elevated transition-colors"
              >
                Show All
              </button>
            )}
          </div>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-secondary border-b border-subtle">
              <th className="text-left pb-3 pr-3">#</th>
              <th className="text-left pb-3 pr-3">Ticker</th>
              <th className="text-left pb-3 pr-3">TF</th>
              <th className="text-left pb-3 pr-3">Signal</th>
              {SORT_COLUMNS.map((col) => (
                <SortHeader key={col.key} label={col.label} sortKey={col.key}
                  currentKey={sortKey} dir={sortDir} onSort={handleSort} align={col.align} />
              ))}
              <th className="text-right pb-3 pr-3">Qty</th>
              <th className="text-right pb-3 pr-3">Max Capital</th>
              <th className="text-right pb-3 pr-3">SL%</th>
              <th className="text-right pb-3 pr-3">PT%</th>
              <th className="text-right pb-3 pr-3">Trail%</th>
              <th className="text-center pb-3"></th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((r, i) => (
              <tr key={`${r.ticker}-${r.timeframe}-${i}`} className={`border-b border-row hover:bg-hover ${isStrategyEnabled(r) ? 'bg-yellow-900/15 ring-1 ring-yellow-500/30' : r.ticker === 'SPY' ? 'bg-blue-900/10' : ''}`}>
                <td className="py-2.5 pr-3 text-muted">{i + 1}</td>
                <td className={`py-2.5 pr-3 font-semibold ${isStrategyEnabled(r) ? 'text-yellow-400' : r.ticker === 'SPY' ? 'text-blue-400' : ''}`}>{r.ticker}{isStrategyEnabled(r) ? ' \u26A1' : ''}</td>
                <td className="py-2.5 pr-3 text-tertiary">{r.timeframe}</td>
                <td className="py-2.5 pr-3 text-xs" title={SIGNAL_DESCRIPTIONS[r.params.signal_type as string]?.summary || ''}>
                  {SIGNAL_LABELS[r.params.signal_type as string] || r.params.signal_type}
                </td>
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
                <td className="py-2.5 pr-3 text-right text-red-400">{formatCurrency(r.max_drawdown)}</td>
                <td className="py-2.5 pr-3 text-right text-green-400 font-semibold">{r.score.toFixed(2)}</td>
                <td className={`py-2.5 pr-3 text-right font-semibold ${r.oos_total_pnl == null ? 'text-muted' : (r.oos_total_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {r.oos_total_pnl != null ? formatCurrency(r.oos_total_pnl) : '--'}
                </td>
                <td className={`py-2.5 pr-3 text-right ${r.oos_profit_factor == null ? 'text-muted' : (r.oos_profit_factor ?? 0) >= 1 ? 'text-green-400' : 'text-red-400'}`}>
                  {r.oos_profit_factor != null ? r.oos_profit_factor.toFixed(2) : '--'}
                </td>
                <td className="py-2.5 pr-3 text-right">
                  {r.mc_win_pct != null ? (
                    <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                      r.mc_win_pct >= 80 ? 'bg-green-500/20 text-green-400' :
                      r.mc_win_pct >= 50 ? 'bg-yellow-500/20 text-yellow-400' :
                      'bg-red-500/20 text-red-400'
                    }`} title={`Monte Carlo: ${r.mc_win_pct}% of 1000 bootstrap simulations profitable\nMedian P&L: ${formatCurrency(r.mc_median_pnl ?? 0)}\n5th pct: ${formatCurrency(r.mc_p5_pnl ?? 0)} | 95th pct: ${formatCurrency(r.mc_p95_pnl ?? 0)}`}>
                      {r.mc_win_pct.toFixed(0)}%
                    </span>
                  ) : <span className="text-muted text-xs">--</span>}
                </td>
                <td className="py-2.5 pr-3 text-right text-xs text-tertiary">{Number(r.params.quantity) || 2}</td>
                <td className="py-2.5 pr-3 text-right text-xs font-medium text-cyan-400">
                  {r.max_entry_price > 0
                    ? formatCurrency((Number(r.params.quantity) || 2) * r.max_entry_price * 100)
                    : '—'}
                </td>
                <td className="py-2.5 pr-3 text-right text-xs text-secondary">{r.params.stop_loss_percent}%</td>
                <td className="py-2.5 pr-3 text-right text-xs text-secondary">{r.params.profit_target_percent}%</td>
                <td className="py-2.5 pr-3 text-right text-xs text-secondary">{r.params.trailing_stop_percent}%</td>
                <td className="py-2.5 text-center whitespace-nowrap">
                  <button
                    onClick={() => onToggleFavorite(r)}
                    className={`px-1.5 py-1 rounded text-sm transition-colors ${
                      isFavorited(r)
                        ? 'text-yellow-400 hover:text-yellow-300'
                        : 'text-muted hover:text-yellow-400'
                    }`}
                    title={isFavorited(r) ? 'Remove from favorites' : 'Add to favorites'}
                  >
                    {isFavorited(r) ? '\u2605' : '\u2606'}
                  </button>
                  <button
                    onClick={() => onDrillDown(r)}
                    disabled={loading}
                    className="ml-1 px-3 py-1 rounded text-xs font-medium transition-colors disabled:opacity-50 bg-green-600/30 hover:bg-green-600 text-green-400 hover:text-white"
                  >
                    Drill Down
                  </button>
                  <button
                    onClick={() => onEditTest(r)}
                    className="ml-1 px-3 py-1 rounded text-xs font-medium transition-colors bg-blue-600/30 hover:bg-blue-600 text-blue-400 hover:text-white"
                  >
                    Edit &amp; Test
                  </button>
                  <button
                    onClick={() => onToggleStrategy(r)}
                    disabled={enabling}
                    className={`ml-1 px-3 py-1 rounded text-xs font-medium transition-colors disabled:opacity-50 ${
                      isStrategyEnabled(r)
                        ? 'bg-red-600/30 hover:bg-red-600 text-red-400 hover:text-white'
                        : 'bg-yellow-600/30 hover:bg-yellow-600 text-yellow-400 hover:text-white'
                    }`}
                  >
                    {isStrategyEnabled(r) ? 'Disable' : 'Enable'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {sorted.length > rowLimit && (
          <div className="mt-4 text-center">
            <button
              onClick={() => setRowLimit((l) => l + 100)}
              className="px-4 py-1.5 rounded bg-elevated text-sm text-secondary hover:text-heading transition-colors"
            >
              Show More ({sorted.length - rowLimit} remaining)
            </button>
          </div>
        )}
      </div>
    </div>
  )
}


// ── Backtest result components ────────────────────────────────────

function SummaryCards({ summary, quantity }: { summary: StockBacktestResponse['summary']; quantity: number }) {
  const maxCapital = summary.max_entry_price > 0 ? quantity * summary.max_entry_price * 100 : 0
  const avgCapital = summary.avg_entry_price > 0 ? quantity * summary.avg_entry_price * 100 : 0

  const cards = [
    { label: 'Total P&L', value: formatCurrency(summary.total_pnl), color: summary.total_pnl >= 0 ? 'text-green-400' : 'text-red-400' },
    { label: 'Win Rate', value: `${summary.win_rate.toFixed(1)}%`, color: summary.win_rate >= 50 ? 'text-green-400' : 'text-yellow-400' },
    { label: 'Trades', value: `${summary.total_trades}`, color: 'text-blue-400', sub: `${summary.winning_trades}W / ${summary.losing_trades}L` },
    { label: 'Profit Factor', value: summary.profit_factor > 0 ? summary.profit_factor.toFixed(2) : '-', color: summary.profit_factor >= 1 ? 'text-green-400' : 'text-red-400' },
    { label: 'Max Drawdown', value: formatCurrency(summary.max_drawdown), color: 'text-red-400' },
    { label: 'Contracts/Trade', value: `${quantity}`, color: 'text-cyan-400', sub: `Avg $${summary.avg_entry_price.toFixed(2)}/ct` },
    { label: 'Max Capital', value: maxCapital > 0 ? formatCurrency(maxCapital) : '-', color: 'text-cyan-400', sub: avgCapital > 0 ? `Avg ${formatCurrency(avgCapital)}` : undefined },
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

function DailyPnLChart({ days, totalPnl }: { days: StockBacktestResponse['days']; totalPnl: number }) {
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

function TradeList({ trades }: { trades: StockBacktestResponse['trades'] }) {
  if (trades.length === 0) return null

  // Compute cumulative P&L and streak
  let cumPnl = 0
  const enriched = trades.map((t) => {
    cumPnl += t.pnl_dollars ?? 0
    return { ...t, cumPnl }
  }).reverse()

  const wins = trades.filter(t => (t.pnl_dollars ?? 0) > 0).length
  const losses = trades.length - wins

  return (
    <div className="bg-surface rounded-lg p-6 overflow-x-auto">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">Trades ({trades.length})</h2>
        <div className="flex gap-3 text-xs">
          <span className="text-green-400">{wins}W</span>
          <span className="text-red-400">{losses}L</span>
          <span className="text-secondary">Avg entry ${(trades.reduce((s, t) => s + t.entry_price, 0) / trades.length).toFixed(2)}</span>
        </div>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-secondary border-b border-subtle">
            <th className="text-center pb-3 pr-3">#</th>
            <th className="text-left pb-3 pr-4">Date / Time</th>
            <th className="text-left pb-3 pr-4">Dir</th>
            <th className="text-left pb-3 pr-4">Signal</th>
            <th className="text-right pb-3 pr-4">Strike</th>
            <th className="text-right pb-3 pr-4">Underlying</th>
            <th className="text-right pb-3 pr-4">Delta</th>
            <th className="text-right pb-3 pr-4">Qty</th>
            <th className="text-right pb-3 pr-4">Entry</th>
            <th className="text-right pb-3 pr-4">Capital</th>
            <th className="text-right pb-3 pr-4">Exit</th>
            <th className="text-right pb-3 pr-4">P&L ($)</th>
            <th className="text-right pb-3 pr-4">P&L (%)</th>
            <th className="text-right pb-3 pr-4">Cum. P&L</th>
            <th className="text-left pb-3 pr-4">Reason</th>
            <th className="text-right pb-3">Hold</th>
          </tr>
        </thead>
        <tbody>
          {enriched.map((t, i) => (
            <tr key={i} className={`border-b border-row hover:bg-hover ${(t.pnl_dollars ?? 0) >= 0 ? '' : 'bg-red-500/5'}`}>
              <td className="py-2 pr-3 text-center text-xs text-muted">{i + 1}</td>
              <td className="py-2 pr-4 text-xs text-secondary">{formatDateShort(t.trade_date)} {formatDt(t.entry_time)}</td>
              <td className={`py-2 pr-4 font-semibold ${t.direction === 'CALL' ? 'text-green-400' : 'text-red-400'}`}>
                {t.direction}
              </td>
              <td className="py-2 pr-4 text-xs text-secondary max-w-[200px] truncate" title={t.entry_reason || ''}>
                {t.entry_reason || '-'}
              </td>
              <td className="py-2 pr-4 text-right">${t.strike?.toFixed(0) ?? '-'}</td>
              <td className="py-2 pr-4 text-right text-secondary">
                {t.underlying_price != null ? `$${t.underlying_price.toFixed(2)}` : '-'}
              </td>
              <td className="py-2 pr-4 text-right text-secondary">
                {t.delta != null ? t.delta.toFixed(2) : '-'}
              </td>
              <td className="py-2 pr-4 text-right text-tertiary">{t.quantity}</td>
              <td className="py-2 pr-4 text-right">${t.entry_price.toFixed(2)}</td>
              <td className="py-2 pr-4 text-right text-cyan-400">{formatCurrency(t.entry_price * t.quantity * 100)}</td>
              <td className="py-2 pr-4 text-right">{t.exit_price != null ? `$${t.exit_price.toFixed(2)}` : '-'}</td>
              <td className={`py-2 pr-4 text-right font-semibold ${(t.pnl_dollars ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {t.pnl_dollars != null ? formatCurrency(t.pnl_dollars) : '-'}
              </td>
              <td className={`py-2 pr-4 text-right ${(t.pnl_percent ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {t.pnl_percent != null ? `${t.pnl_percent > 0 ? '+' : ''}${t.pnl_percent.toFixed(1)}%` : '-'}
              </td>
              <td className={`py-2 pr-4 text-right text-xs font-medium ${t.cumPnl >= 0 ? 'text-green-400/70' : 'text-red-400/70'}`}>
                {formatCurrency(t.cumPnl)}
              </td>
              <td className="py-2 pr-4 text-xs">
                <span
                  className={`px-2 py-0.5 rounded ${REASON_COLORS[t.exit_reason || ''] || 'bg-elevated text-tertiary'}`}
                  title={t.exit_detail || ''}
                >
                  {t.exit_reason?.replace(/_/g, ' ') || '-'}
                </span>
              </td>
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

function Spinner({ text }: { text: string }) {
  return (
    <div className="flex items-center justify-center py-12 text-secondary">
      <svg className="animate-spin h-6 w-6 mr-3" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      {text}
    </div>
  )
}
