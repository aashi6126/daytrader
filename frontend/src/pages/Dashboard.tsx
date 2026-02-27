import { useEffect, useMemo, useState } from 'react'
import { useWebSocket } from '../hooks/useWebSocket'
import { DailyStats } from '../components/DailyStats'
import { PnLChart } from '../components/PnLChart'
import { OpenPositions } from '../components/OpenPositions'
import { TradeTable } from '../components/TradeTable'
import { CandlestickChart } from '../components/CandlestickChart'
import { StrategyCards } from '../components/StrategyCards'
import { fetchAnalytics, fetchCandles, fetchChartMarkers, fetchDailyStats, fetchMarketOrderOverride, fetchMarketOverview, fetchPivotLevels, fetchPnLData, fetchPnLSummary, fetchWindowOverride, setMarketOrderOverride, setWindowOverride, type AnalyticsData, type CandleData, type ChartMarker, type MarketOverview, type PivotLevels } from '../api/dashboard'
import { fetchTrades } from '../api/trades'
import { getStrategyStatus, type EnabledStrategyEntry } from '../api/stockBacktest'
import type { DailyStats as DailyStatsType, PnLDataPoint, PnLSummaryData, Trade } from '../types'
import { isMarketOpen } from '../utils/format'

function todayStr() {
  return new Date().toLocaleDateString('en-CA') // YYYY-MM-DD
}

function lastTradingDay() {
  const d = new Date()
  const day = d.getDay() // 0=Sun, 6=Sat
  if (day === 0) d.setDate(d.getDate() - 2)
  else if (day === 6) d.setDate(d.getDate() - 1)
  return d.toLocaleDateString('en-CA')
}

function formatDateLabel(dateStr: string) {
  const d = new Date(dateStr + 'T12:00:00')
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
}

function shiftDate(dateStr: string, days: number) {
  const d = new Date(dateStr + 'T12:00:00')
  d.setDate(d.getDate() + days)
  return d.toLocaleDateString('en-CA')
}

export default function Dashboard() {
  const [selectedDate, setSelectedDate] = useState(lastTradingDay)
  const isToday = selectedDate === todayStr()

  const [stats, setStats] = useState<DailyStatsType | null>(null)
  const [pnlData, setPnlData] = useState<PnLDataPoint[]>([])
  const [totalPnl, setTotalPnl] = useState(0)
  const [openTrades, setOpenTrades] = useState<Trade[]>([])
  const [recentTrades, setRecentTrades] = useState<Trade[]>([])
  const [candles, setCandles] = useState<CandleData[]>([])
  const [markers, setMarkers] = useState<ChartMarker[]>([])
  const [pivots, setPivots] = useState<PivotLevels | null>(null)
  const [pnlPeriod, setPnlPeriod] = useState<'daily' | 'weekly' | 'monthly'>('daily')
  const [summaryData, setSummaryData] = useState<PnLSummaryData | null>(null)
  const [ignoreWindows, setIgnoreWindows] = useState(false)
  const [useMarketOrders, setUseMarketOrders] = useState(false)
  const [enabledStrategies, setEnabledStrategies] = useState<EnabledStrategyEntry[]>([])
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null)
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null)
  const [analyticsDays, setAnalyticsDays] = useState(30)
  const [candleFreq, setCandleFreq] = useState(5)
  const [market, setMarket] = useState<MarketOverview | null>(null)

  const TRADING_WINDOWS = [
    { label: 'Morning', start: { h: 9, m: 45 }, end: { h: 11, m: 15 }, enabled: true },
    { label: 'Afternoon', start: { h: 12, m: 45 }, end: { h: 14, m: 50 }, enabled: false },
  ]

  const [now, setNow] = useState(new Date())
  useEffect(() => {
    if (!isMarketOpen()) return
    const t = setInterval(() => setNow(new Date()), 10_000)
    return () => clearInterval(t)
  }, [])

  const activeWindow = useMemo(() => {
    const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }))
    const mins = et.getHours() * 60 + et.getMinutes()
    return TRADING_WINDOWS.find(
      (w) => w.enabled && mins >= w.start.h * 60 + w.start.m && mins < w.end.h * 60 + w.end.m
    ) ?? null
  }, [now])

  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const { lastMessage, isConnected } = useWebSocket(
    `${wsProtocol}://${window.location.host}/ws/dashboard`
  )

  const loadData = (dateStr?: string) => {
    const d = dateStr || selectedDate
    const dateParam = d === todayStr() ? undefined : d
    fetchDailyStats(dateParam).then(setStats).catch(() => {})
    fetchPnLData(dateParam).then((r) => {
      setPnlData(r.data_points)
      setTotalPnl(r.total_pnl)
    }).catch(() => {})
    fetchTrades({ trade_date: d, per_page: 50 }).then((r) => {
      setOpenTrades(
        r.trades.filter((t) =>
          ['FILLED', 'STOP_LOSS_PLACED', 'EXITING', 'PENDING'].includes(t.status)
        )
      )
      setRecentTrades(r.trades.slice(0, 10))
    }).catch(() => {})
  }

  useEffect(() => {
    loadData(selectedDate)
    fetchMarketOverview().then(setMarket).catch(() => {})
    // Only auto-refresh when viewing today and market is open
    if (isToday && isMarketOpen()) {
      const interval = setInterval(() => {
        loadData(selectedDate)
        fetchMarketOverview().then(setMarket).catch(() => {})
      }, 30000)
      return () => clearInterval(interval)
    }
  }, [selectedDate])

  useEffect(() => {
    if (pnlPeriod === 'daily') {
      setSummaryData(null)
    } else {
      fetchPnLSummary(pnlPeriod).then(setSummaryData).catch(() => {})
    }
  }, [pnlPeriod])

  useEffect(() => {
    fetchWindowOverride().then((d) => setIgnoreWindows(d.ignore_trading_windows)).catch(() => {})
    fetchMarketOrderOverride().then((d) => setUseMarketOrders(d.use_market_orders)).catch(() => {})
    getStrategyStatus().then((r) => {
      setEnabledStrategies(r.strategies)
      if (r.strategies.length > 0 && !selectedTicker) {
        setSelectedTicker(r.strategies[0].ticker)
      }
    }).catch(() => {})
  }, [])

  useEffect(() => {
    fetchAnalytics(analyticsDays).then(setAnalytics).catch(() => {})
  }, [analyticsDays])

  const toggleIgnoreWindows = () => {
    const next = !ignoreWindows
    setIgnoreWindows(next)
    setWindowOverride(next).catch(() => setIgnoreWindows(!next))
  }

  const toggleMarketOrders = () => {
    const next = !useMarketOrders
    setUseMarketOrders(next)
    setMarketOrderOverride(next).catch(() => setUseMarketOrders(!next))
  }

  // Reset candle frequency when ticker/strategy changes
  useEffect(() => {
    if (!selectedTicker) return
    const strategy = enabledStrategies.find((s) => s.ticker === selectedTicker)
    const stratFreq = parseInt(strategy?.timeframe || '5') || 5
    setCandleFreq(stratFreq)
  }, [selectedTicker, enabledStrategies])

  // Fetch candles and markers for selected ticker + date + frequency
  useEffect(() => {
    if (!selectedTicker) return
    setCandles([])
    setMarkers([])
    setPivots(null)
    const dateParam = isToday ? undefined : selectedDate

    const load = () => {
      fetchCandles(selectedTicker, candleFreq, dateParam).then(setCandles).catch(() => {})
      fetchChartMarkers(selectedTicker, dateParam).then(setMarkers).catch(() => {})
      fetchPivotLevels(selectedTicker, dateParam).then(setPivots).catch(() => setPivots(null))
    }
    load()
    if (!isMarketOpen() || !isToday) return
    const interval = setInterval(load, 60_000)
    return () => clearInterval(interval)
  }, [selectedTicker, candleFreq, selectedDate])

  useEffect(() => {
    if (!lastMessage) return
    if (
      ['trade_created', 'trade_filled', 'trade_closed', 'trade_cancelled'].includes(
        lastMessage.event
      )
    ) {
      loadData()
      if (selectedTicker) {
        const dateParam = isToday ? undefined : selectedDate
        fetchChartMarkers(selectedTicker, dateParam).then(setMarkers).catch(() => {})
      }
    }
  }, [lastMessage])

  const hasOpenPositions = openTrades.length > 0
  const chartTitle = selectedTicker
    ? `${selectedTicker} ${candleFreq}m`
    : 'No strategy selected'

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold">DayTrader</h1>
          <span className="text-xs px-2 py-0.5 rounded bg-yellow-900/40 text-yellow-300 ring-1 ring-yellow-500/40 font-medium">
            DRY RUN
          </span>
          {market?.spy?.price != null && (
            <span className="text-xs px-2 py-0.5 rounded font-medium ring-1 bg-surface text-heading ring-subtle">
              SPY {market.spy.price.toFixed(1)}
              {market.spy.change_percent != null && (
                <span className={market.spy.change_percent >= 0 ? ' text-green-400' : ' text-red-400'}>
                  {' '}{market.spy.change_percent >= 0 ? '+' : ''}{market.spy.change_percent.toFixed(1)}%
                </span>
              )}
            </span>
          )}
          {market?.qqq?.price != null && (
            <span className="text-xs px-2 py-0.5 rounded font-medium ring-1 bg-surface text-heading ring-subtle">
              QQQ {market.qqq.price.toFixed(1)}
              {market.qqq.change_percent != null && (
                <span className={market.qqq.change_percent >= 0 ? ' text-green-400' : ' text-red-400'}>
                  {' '}{market.qqq.change_percent >= 0 ? '+' : ''}{market.qqq.change_percent.toFixed(1)}%
                </span>
              )}
            </span>
          )}
          {market?.vix?.price != null && (
            <span className={`text-xs px-2 py-0.5 rounded font-medium ring-1 ${
              market.vix.price >= 25
                ? 'bg-red-900/40 text-red-300 ring-red-500/40'
                : market.vix.price >= 18
                ? 'bg-yellow-900/40 text-yellow-300 ring-yellow-500/40'
                : 'bg-green-900/40 text-green-300 ring-green-500/40'
            }`}>
              VIX {market.vix.price.toFixed(1)}
              {market.vix.change != null && (
                <span className={market.vix.change >= 0 ? ' text-red-400' : ' text-green-400'}>
                  {' '}{market.vix.change >= 0 ? '+' : ''}{market.vix.change.toFixed(1)}
                </span>
              )}
            </span>
          )}
          <div className="flex items-center gap-1 ml-2">
            <button
              onClick={() => setSelectedDate((d) => shiftDate(d, -1))}
              className="px-1.5 py-0.5 rounded bg-elevated text-secondary hover:text-heading text-sm"
            >
              &larr;
            </button>
            <input
              type="date"
              value={selectedDate}
              max={todayStr()}
              onChange={(e) => setSelectedDate(e.target.value)}
              className="bg-surface text-heading text-xs px-2 py-1 rounded ring-1 ring-subtle focus:ring-blue-500/50 outline-none"
            />
            <button
              onClick={() => setSelectedDate((d) => {
                const next = shiftDate(d, 1)
                return next > todayStr() ? todayStr() : next
              })}
              disabled={isToday}
              className="px-1.5 py-0.5 rounded bg-elevated text-secondary hover:text-heading text-sm disabled:opacity-30"
            >
              &rarr;
            </button>
            {!isToday && (
              <button
                onClick={() => setSelectedDate(todayStr())}
                className="px-2 py-0.5 rounded text-xs font-medium bg-blue-600/30 text-blue-300 hover:bg-blue-600/50 transition-colors"
              >
                Today
              </button>
            )}
          </div>
        </div>
        <div className="flex items-center gap-3">
          {/* Trading windows */}
          {TRADING_WINDOWS.map((w) => {
            const fmt = (t: { h: number; m: number }) => {
              const hr = t.h > 12 ? t.h - 12 : t.h
              const ampm = t.h >= 12 ? 'PM' : 'AM'
              return `${hr}:${String(t.m).padStart(2, '0')} ${ampm}`
            }
            const isActive = activeWindow?.label === w.label
            return (
              <span
                key={w.label}
                className={`text-xs px-2 py-1 rounded ${
                  ignoreWindows
                    ? 'bg-inset text-muted line-through'
                    : !w.enabled
                    ? 'bg-inset text-muted line-through'
                    : isActive
                    ? 'bg-green-900/50 text-green-400 ring-1 ring-green-500/50'
                    : 'bg-surface text-secondary'
                }`}
              >
                {fmt(w.start)}–{fmt(w.end)}
                {isActive ? ' \u25CF' : ''}
              </span>
            )
          })}
          <button
            onClick={toggleIgnoreWindows}
            className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
              ignoreWindows
                ? 'bg-yellow-600/30 text-yellow-300 ring-1 ring-yellow-500/50'
                : 'bg-elevated text-tertiary hover:bg-elevated ring-1 ring-default'
            }`}
          >
            {ignoreWindows ? 'Bypassed' : 'Ignore'}
          </button>
          <button
            onClick={toggleMarketOrders}
            className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
              useMarketOrders
                ? 'bg-green-600/30 text-green-300 ring-1 ring-green-500/50'
                : 'bg-elevated text-tertiary hover:bg-elevated ring-1 ring-default'
            }`}
          >
            {useMarketOrders ? 'Market' : 'Limit'}
          </button>
          <div className="w-px h-4 bg-subtle" />
          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-400' : 'bg-red-400'}`} />
            <span className="text-xs text-secondary">{isConnected ? 'Live' : 'Off'}</span>
          </div>
        </div>
      </div>

      {/* Strategy cards */}
      <StrategyCards
        strategies={enabledStrategies}
        selectedTicker={selectedTicker}
        onSelect={setSelectedTicker}
      />

      {/* Daily stats bar */}
      <DailyStats stats={stats} />

      {/* Candlestick Chart — driven by selected strategy */}
      {selectedTicker && (
        <>
          <div className="flex gap-1">
            {[1, 5, 15].map((f) => (
              <button
                key={f}
                onClick={() => setCandleFreq(f)}
                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                  candleFreq === f
                    ? 'bg-blue-600 text-white'
                    : 'bg-elevated text-tertiary hover:bg-elevated'
                }`}
              >
                {f}m
              </button>
            ))}
          </div>
          {candles.length > 0
            ? <CandlestickChart data={candles} markers={markers} title={chartTitle} pivots={pivots} />
            : (
              <div className="bg-surface rounded-lg ring-1 ring-subtle p-4">
                <h3 className="text-sm font-medium text-secondary">{chartTitle}</h3>
                <div className="flex items-center justify-center h-[300px] text-muted text-sm">
                  Market closed — no candle data available
                </div>
              </div>
            )
          }
        </>
      )}

      {/* Open positions — full width, only shown when there are positions */}
      {hasOpenPositions && (
        <OpenPositions trades={openTrades} onClose={loadData} />
      )}

      {/* P&L Chart — full width */}
      <div>
        <div className="flex gap-1 mb-3">
          {(['daily', 'weekly', 'monthly'] as const).map((p) => (
            <button
              key={p}
              onClick={() => setPnlPeriod(p)}
              className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                pnlPeriod === p
                  ? 'bg-blue-600 text-white'
                  : 'bg-elevated text-tertiary hover:bg-elevated'
              }`}
            >
              {p.charAt(0).toUpperCase() + p.slice(1)}
            </button>
          ))}
        </div>
        <PnLChart
          period={pnlPeriod}
          data={pnlData}
          totalPnl={totalPnl}
          summaryData={summaryData}
          isToday={isToday}
        />
      </div>

      {/* Post-Trade Analytics */}
      {analytics && analytics.total_trades > 0 && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">Analytics</h2>
            <div className="flex items-center gap-2">
              {[7, 14, 30, 90].map((d) => (
                <button
                  key={d}
                  onClick={() => setAnalyticsDays(d)}
                  className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                    analyticsDays === d
                      ? 'bg-blue-600 text-white'
                      : 'bg-elevated text-tertiary hover:bg-elevated'
                  }`}
                >
                  {d}d
                </button>
              ))}
              <span className="text-xs text-secondary ml-2">
                {analytics.total_trades} trades
              </span>
            </div>
          </div>

          {/* Streak */}
          {analytics.streak.current_count > 0 && (
            <div className="flex gap-3 text-xs">
              <span className={`px-2 py-1 rounded ${
                analytics.streak.current_type === 'win'
                  ? 'bg-green-900/40 text-green-400'
                  : 'bg-red-900/40 text-red-400'
              }`}>
                Current: {analytics.streak.current_count} {analytics.streak.current_type}{analytics.streak.current_count > 1 ? 's' : ''} in a row
              </span>
              <span className="px-2 py-1 rounded bg-elevated text-secondary">
                Best streak: {analytics.streak.longest_win}W / {analytics.streak.longest_loss}L
              </span>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* By Hour */}
            <div className="bg-surface rounded-lg p-4">
              <h3 className="text-sm font-medium text-secondary mb-3">PnL by Hour (ET)</h3>
              <div className="space-y-1.5">
                {analytics.by_hour.filter((h) => h.total_trades > 0).map((h) => (
                  <div key={h.hour} className="flex items-center gap-2 text-xs">
                    <span className="w-12 text-secondary">{h.label}</span>
                    <div className="flex-1 h-4 bg-elevated rounded overflow-hidden relative">
                      <div
                        className={`h-full rounded ${h.total_pnl >= 0 ? 'bg-green-600/60' : 'bg-red-600/60'}`}
                        style={{ width: `${Math.min(Math.abs(h.total_pnl) / Math.max(...analytics.by_hour.map((x) => Math.abs(x.total_pnl) || 1)) * 100, 100)}%` }}
                      />
                    </div>
                    <span className={`w-16 text-right font-mono ${h.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      ${h.total_pnl >= 0 ? '+' : ''}{h.total_pnl.toFixed(0)}
                    </span>
                    <span className="w-10 text-right text-secondary">{h.win_rate}%</span>
                    <span className="w-6 text-right text-muted">{h.total_trades}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* By Strategy */}
            <div className="bg-surface rounded-lg p-4">
              <h3 className="text-sm font-medium text-secondary mb-3">PnL by Strategy</h3>
              <div className="space-y-1.5">
                {analytics.by_strategy.map((s) => (
                  <div key={s.strategy} className="flex items-center gap-2 text-xs">
                    <span className="w-24 text-secondary truncate">{s.strategy}</span>
                    <div className="flex-1 h-4 bg-elevated rounded overflow-hidden">
                      <div
                        className={`h-full rounded ${s.total_pnl >= 0 ? 'bg-green-600/60' : 'bg-red-600/60'}`}
                        style={{ width: `${Math.min(Math.abs(s.total_pnl) / Math.max(...analytics.by_strategy.map((x) => Math.abs(x.total_pnl) || 1)) * 100, 100)}%` }}
                      />
                    </div>
                    <span className={`w-16 text-right font-mono ${s.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      ${s.total_pnl >= 0 ? '+' : ''}{s.total_pnl.toFixed(0)}
                    </span>
                    <span className="w-10 text-right text-secondary">{s.win_rate}%</span>
                    <span className="w-8 text-right text-secondary">PF {s.profit_factor}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* By Day of Week */}
            <div className="bg-surface rounded-lg p-4">
              <h3 className="text-sm font-medium text-secondary mb-3">PnL by Day of Week</h3>
              <div className="space-y-1.5">
                {analytics.by_day_of_week.filter((d) => d.total_trades > 0).map((d) => (
                  <div key={d.day} className="flex items-center gap-2 text-xs">
                    <span className="w-8 text-secondary">{d.label}</span>
                    <div className="flex-1 h-4 bg-elevated rounded overflow-hidden">
                      <div
                        className={`h-full rounded ${d.total_pnl >= 0 ? 'bg-green-600/60' : 'bg-red-600/60'}`}
                        style={{ width: `${Math.min(Math.abs(d.total_pnl) / Math.max(...analytics.by_day_of_week.map((x) => Math.abs(x.total_pnl) || 1)) * 100, 100)}%` }}
                      />
                    </div>
                    <span className={`w-16 text-right font-mono ${d.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      ${d.total_pnl >= 0 ? '+' : ''}{d.total_pnl.toFixed(0)}
                    </span>
                    <span className="w-10 text-right text-secondary">{d.win_rate}%</span>
                    <span className="w-6 text-right text-muted">{d.total_trades}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* By Hold Time */}
            <div className="bg-surface rounded-lg p-4">
              <h3 className="text-sm font-medium text-secondary mb-3">Win Rate by Hold Time</h3>
              <div className="space-y-1.5">
                {analytics.by_hold_time.filter((h) => h.total_trades > 0).map((h) => (
                  <div key={h.label} className="flex items-center gap-2 text-xs">
                    <span className="w-16 text-secondary">{h.label}</span>
                    <div className="flex-1 h-4 bg-elevated rounded overflow-hidden">
                      <div
                        className="h-full rounded bg-blue-600/60"
                        style={{ width: `${h.win_rate}%` }}
                      />
                    </div>
                    <span className="w-10 text-right text-secondary">{h.win_rate}%</span>
                    <span className={`w-16 text-right font-mono ${h.avg_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      ${h.avg_pnl >= 0 ? '+' : ''}{h.avg_pnl.toFixed(0)}
                    </span>
                    <span className="w-6 text-right text-muted">{h.total_trades}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Recent Trades */}
      <TradeTable
        trades={recentTrades}
        title={isToday ? 'Recent Trades' : `Trades — ${formatDateLabel(selectedDate)}`}
        compact
        onRetake={() => loadData(selectedDate)}
      />
    </div>
  )
}
