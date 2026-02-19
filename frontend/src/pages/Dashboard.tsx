import { useEffect, useMemo, useState } from 'react'
import { useWebSocket } from '../hooks/useWebSocket'
import { DailyStats } from '../components/DailyStats'
import { PnLChart } from '../components/PnLChart'
import { OpenPositions } from '../components/OpenPositions'
import { TradeTable } from '../components/TradeTable'
import { fetchActiveStrategy, fetchDailyStats, fetchPnLData, fetchPnLSummary, fetchSpyPrice, fetchWindowOverride, setWindowOverride, type ActiveStrategy, type SpyPrice } from '../api/dashboard'
import { fetchTrades } from '../api/trades'
import type { DailyStats as DailyStatsType, PnLDataPoint, PnLSummaryData, Trade } from '../types'

export default function Dashboard() {
  const [stats, setStats] = useState<DailyStatsType | null>(null)
  const [pnlData, setPnlData] = useState<PnLDataPoint[]>([])
  const [totalPnl, setTotalPnl] = useState(0)
  const [openTrades, setOpenTrades] = useState<Trade[]>([])
  const [recentTrades, setRecentTrades] = useState<Trade[]>([])
  const [spy, setSpy] = useState<SpyPrice | null>(null)
  const [pnlPeriod, setPnlPeriod] = useState<'daily' | 'weekly' | 'monthly'>('daily')
  const [summaryData, setSummaryData] = useState<PnLSummaryData | null>(null)
  const [ignoreWindows, setIgnoreWindows] = useState(false)
  const [strategy, setStrategy] = useState<ActiveStrategy | null>(null)
  const TRADING_WINDOWS = [
    { label: 'Morning', start: { h: 9, m: 45 }, end: { h: 11, m: 15 }, enabled: true },
    { label: 'Afternoon', start: { h: 12, m: 45 }, end: { h: 14, m: 50 }, enabled: false },
  ]

  const [now, setNow] = useState(new Date())
  useEffect(() => {
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

  const loadData = () => {
    fetchDailyStats().then(setStats).catch(() => {})
    fetchPnLData().then((d) => {
      setPnlData(d.data_points)
      setTotalPnl(d.total_pnl)
    }).catch(() => {})
    fetchTrades({ per_page: 50 }).then((d) => {
      setOpenTrades(
        d.trades.filter((t) =>
          ['FILLED', 'STOP_LOSS_PLACED', 'EXITING', 'PENDING'].includes(t.status)
        )
      )
      setRecentTrades(d.trades.slice(0, 10))
    }).catch(() => {})
  }

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 30000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (pnlPeriod === 'daily') {
      setSummaryData(null)
    } else {
      fetchPnLSummary(pnlPeriod).then(setSummaryData).catch(() => {})
    }
  }, [pnlPeriod])

  useEffect(() => {
    fetchWindowOverride().then((d) => setIgnoreWindows(d.ignore_trading_windows)).catch(() => {})
    fetchActiveStrategy().then(setStrategy).catch(() => {})
  }, [])

  const toggleIgnoreWindows = () => {
    const next = !ignoreWindows
    setIgnoreWindows(next)
    setWindowOverride(next).catch(() => setIgnoreWindows(!next))
  }

  useEffect(() => {
    fetchSpyPrice().then(setSpy).catch(() => {})
    const interval = setInterval(() => {
      fetchSpyPrice().then(setSpy).catch(() => {})
    }, 10_000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (!lastMessage) return
    if (
      ['trade_created', 'trade_filled', 'trade_closed', 'trade_cancelled'].includes(
        lastMessage.event
      )
    ) {
      loadData()
    }
  }, [lastMessage])

  const hasOpenPositions = openTrades.length > 0

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold">0DTE SPY Trader</h1>
          {spy?.price != null && (
            <div className="flex items-baseline gap-2">
              <span className="text-lg font-semibold text-primary">
                SPY ${spy.price.toFixed(2)}
              </span>
              {spy.change != null && (
                <span className={`text-sm font-medium ${
                  spy.change >= 0 ? 'text-green-400' : 'text-red-400'
                }`}>
                  {spy.change >= 0 ? '+' : ''}{spy.change.toFixed(2)} ({spy.change_percent?.toFixed(2)}%)
                </span>
              )}
            </div>
          )}
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
          <div className="w-px h-4 bg-subtle" />
          {strategy && (
            <span className={`text-xs px-2 py-1 rounded font-medium ${
              strategy.strategy === 'orb_auto'
                ? 'bg-blue-900/50 text-blue-300 ring-1 ring-blue-500/50'
                : strategy.strategy === 'tradingview'
                ? 'bg-purple-900/50 text-purple-300 ring-1 ring-purple-500/50'
                : 'bg-inset text-muted'
            }`}>
              {strategy.description}
            </span>
          )}
          <div className="w-px h-4 bg-subtle" />
          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-400' : 'bg-red-400'}`} />
            <span className="text-xs text-secondary">{isConnected ? 'Live' : 'Off'}</span>
          </div>
        </div>
      </div>

      {/* Daily stats bar */}
      <DailyStats stats={stats} />

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
        />
      </div>

      {/* Recent Trades */}
      <TradeTable trades={recentTrades} title="Recent Trades" compact onRetake={loadData} />
    </div>
  )
}
