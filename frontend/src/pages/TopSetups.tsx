import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import {
  getSavedResults,
  type StockOptimizeResultEntry, type StockBacktestResponse,
  type StockBacktestParams,
  runStockBacktest,
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


interface TopSetupsProps {
  onApplySpyParams: (params: Record<string, number | string | boolean>) => void
}

export default function TopSetups({ onApplySpyParams }: TopSetupsProps) {
  const [savedResults, setSavedResults] = useState<StockOptimizeResultEntry[]>([])
  const [loadingResults, setLoadingResults] = useState(true)

  // Drill-down backtest state
  const [btResult, setBtResult] = useState<StockBacktestResponse | null>(null)
  const [btLoading, setBtLoading] = useState(false)
  const [btError, setBtError] = useState<string | null>(null)
  const [btLabel, setBtLabel] = useState('')
  const [showDrillDown, setShowDrillDown] = useState(false)
  const drillDownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getSavedResults()
      .then(setSavedResults)
      .catch(() => {})
      .finally(() => setLoadingResults(false))
  }, [])

  const drillDown = (entry: StockOptimizeResultEntry) => {
    // SPY tiles → apply params to backtest form and switch tab
    if (entry.ticker === 'SPY') {
      onApplySpyParams(entry.params)
      return
    }

    // Stock tiles → drill-down view
    setBtLoading(true)
    setBtError(null)
    setBtResult(null)
    setShowDrillDown(true)
    setBtLabel(`${entry.ticker} @ ${entry.timeframe} — ${SIGNAL_LABELS[entry.params.signal_type as string] || entry.params.signal_type}`)

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
      max_daily_loss: 500,
      max_consecutive_losses: 3,
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

  return (
    <div className="space-y-6">
      {loadingResults ? (
        <Spinner text="Loading saved results..." />
      ) : savedResults.length === 0 ? (
        <div className="bg-surface rounded-lg p-8 text-center text-secondary">
          No saved results found. Run the optimizer first.
        </div>
      ) : (
        <ResultsTable results={savedResults} onDrillDown={drillDown} loading={btLoading} />
      )}

      {/* Drill-down view */}
      <div ref={drillDownRef} />
      {showDrillDown && (
        <>
          {btLoading && <Spinner text="Running backtest..." />}
          {btError && <div className="bg-surface rounded-lg p-4 text-red-400">{btError}</div>}
          {btResult && !btLoading && (
            <>
              <div className="bg-surface rounded-lg p-4 flex items-center justify-between">
                <h2 className="text-lg font-semibold">{btLabel}</h2>
                <button
                  onClick={() => { setShowDrillDown(false); setBtResult(null) }}
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
        </>
      )}
    </div>
  )
}


// ── Results table ─────────────────────────────────────────────────

type SortKey = 'total_pnl' | 'total_trades' | 'win_rate' | 'profit_factor' | 'max_drawdown' | 'avg_hold_minutes' | 'score'
type SortDir = 'asc' | 'desc'

const SORT_COLUMNS: { key: SortKey; label: string; align: 'left' | 'right' }[] = [
  { key: 'total_pnl', label: 'P&L', align: 'right' },
  { key: 'total_trades', label: 'Trades', align: 'right' },
  { key: 'win_rate', label: 'WR%', align: 'right' },
  { key: 'profit_factor', label: 'PF', align: 'right' },
  { key: 'max_drawdown', label: 'MaxDD', align: 'right' },
  { key: 'avg_hold_minutes', label: 'Avg Hold', align: 'right' },
  { key: 'score', label: 'Score', align: 'right' },
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
}: {
  results: StockOptimizeResultEntry[]
  onDrillDown: (entry: StockOptimizeResultEntry) => void
  loading: boolean
}) {
  const [sortKey, setSortKey] = useState<SortKey>('total_pnl')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [filterTicker, setFilterTicker] = useState<string | null>('TSLA')

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const filtered = useMemo(() => {
    if (!filterTicker) return results
    return results.filter((r) => r.ticker === filterTicker)
  }, [results, filterTicker])

  const sorted = useMemo(() => {
    const copy = [...filtered]
    copy.sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      return sortDir === 'desc' ? bv - av : av - bv
    })
    return copy
  }, [filtered, sortKey, sortDir])

  // Group best per ticker for the summary (by max PnL)
  const bestPerTicker = new Map<string, StockOptimizeResultEntry>()
  for (const r of results) {
    const existing = bestPerTicker.get(r.ticker)
    if (!existing || r.total_pnl > existing.total_pnl) {
      bestPerTicker.set(r.ticker, r)
    }
  }
  const tickerSummary = Array.from(bestPerTicker.values()).sort((a, b) => b.total_pnl - a.total_pnl)

  return (
    <div className="space-y-4">
      {/* Ticker cards summary */}
      {tickerSummary.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-2">
          {tickerSummary.map((r) => {
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
                  <span className="font-bold text-sm">{r.ticker}</span>
                  <span className="text-xs text-muted">{r.timeframe}</span>
                </div>
                <p className={`text-sm font-semibold ${r.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {formatCurrency(r.total_pnl)}
                </p>
                <p className="text-xs text-secondary">
                  {SIGNAL_LABELS[r.params.signal_type as string] || r.params.signal_type} &middot; {r.win_rate.toFixed(0)}% WR
                </p>
              </div>
            )
          })}
        </div>
      )}

      {/* Full results table */}
      <div className="bg-surface rounded-lg p-6 overflow-x-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">
            {filterTicker ? `${filterTicker} Results (${sorted.length})` : `All Results (${results.length})`}
          </h2>
          {filterTicker && (
            <button
              onClick={() => setFilterTicker(null)}
              className="text-xs text-secondary hover:text-heading px-2 py-1 rounded bg-elevated hover:bg-elevated transition-colors"
            >
              Show All
            </button>
          )}
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
              <th className="text-right pb-3 pr-3">SL%</th>
              <th className="text-right pb-3 pr-3">PT%</th>
              <th className="text-right pb-3 pr-3">Trail%</th>
              <th className="text-center pb-3"></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={`${r.ticker}-${r.timeframe}-${i}`} className={`border-b border-row hover:bg-hover ${r.ticker === 'SPY' ? 'bg-blue-900/10' : ''}`}>
                <td className="py-2.5 pr-3 text-muted">{i + 1}</td>
                <td className={`py-2.5 pr-3 font-semibold ${r.ticker === 'SPY' ? 'text-blue-400' : ''}`}>{r.ticker}</td>
                <td className="py-2.5 pr-3 text-tertiary">{r.timeframe}</td>
                <td className="py-2.5 pr-3 text-xs">
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
                <td className="py-2.5 pr-3 text-right text-tertiary">{r.avg_hold_minutes.toFixed(0)}m</td>
                <td className="py-2.5 pr-3 text-right text-green-400 font-semibold">{r.score.toFixed(2)}</td>
                <td className="py-2.5 pr-3 text-right text-xs text-secondary">{r.params.stop_loss_percent}%</td>
                <td className="py-2.5 pr-3 text-right text-xs text-secondary">{r.params.profit_target_percent}%</td>
                <td className="py-2.5 pr-3 text-right text-xs text-secondary">{r.params.trailing_stop_percent}%</td>
                <td className="py-2.5 text-center">
                  <button
                    onClick={() => onDrillDown(r)}
                    disabled={loading}
                    className={`px-3 py-1 rounded text-xs font-medium transition-colors disabled:opacity-50 ${
                      r.ticker === 'SPY'
                        ? 'bg-blue-600/30 hover:bg-blue-600 text-blue-400 hover:text-white'
                        : 'bg-green-600/30 hover:bg-green-600 text-green-400 hover:text-white'
                    }`}
                  >
                    {r.ticker === 'SPY' ? 'Apply' : 'Drill Down'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}


// ── Backtest result components ────────────────────────────────────

function SummaryCards({ summary }: { summary: StockBacktestResponse['summary'] }) {
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
              <td className="py-2 pr-4 text-right">${t.strike?.toFixed(0) ?? '-'}</td>
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
              <td className={`py-2 pr-4 text-right font-semibold ${(t.pnl_dollars ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {t.pnl_dollars != null ? formatCurrency(t.pnl_dollars) : '-'}
              </td>
              <td className={`py-2 pr-4 text-right ${(t.pnl_percent ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {t.pnl_percent != null ? `${t.pnl_percent > 0 ? '+' : ''}${t.pnl_percent.toFixed(1)}%` : '-'}
              </td>
              <td className="py-2 pr-4 text-xs">
                <span className={`px-2 py-0.5 rounded ${REASON_COLORS[t.exit_reason || ''] || 'bg-elevated text-tertiary'}`}>
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
