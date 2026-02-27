import { useState } from 'react'
import type { Trade } from '../types'
import { formatCurrency, formatPercent, formatTime, statusColor, pnlColor } from '../utils/format'
import { TradeActivityLog } from './TradeActivityLog'
import { retakeTrade } from '../api/trades'

interface Props {
  trades: Trade[]
  title?: string
  compact?: boolean
  onRetake?: () => void
}

const COL_COUNT = 15
const SIGNAL_SOURCES = new Set(['tradingview', 'orb_auto', 'strategy_signal'])

const isSignalTrade = (t: Trade) => SIGNAL_SOURCES.has(t.source ?? '')

export function TradeTable({ trades, title, compact = false, onRetake }: Props) {
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [retaking, setRetaking] = useState<number | null>(null)
  const [retakeMsg, setRetakeMsg] = useState<string | null>(null)
  const [hideCancelled, setHideCancelled] = useState(true)
  const [sourceFilter, setSourceFilter] = useState<'all' | 'signal' | 'manual'>('signal')

  const afterSource =
    sourceFilter === 'signal'
      ? trades.filter(isSignalTrade)
      : sourceFilter === 'manual'
        ? trades.filter((t) => !isSignalTrade(t))
        : trades
  const filtered = hideCancelled ? afterSource.filter((t) => t.status !== 'CANCELLED') : afterSource
  const cancelledCount = trades.length - trades.filter((t) => t.status !== 'CANCELLED').length
  const signalCount = trades.filter(isSignalTrade).length
  const manualCount = trades.length - signalCount

  if (trades.length === 0) {
    return (
      <div className="bg-surface rounded-lg p-6">
        {title && <h2 className="text-lg font-semibold mb-4">{title}</h2>}
        <p className="text-muted text-center py-8">No trades to display</p>
      </div>
    )
  }

  return (
    <div className="bg-surface rounded-lg p-6 overflow-x-auto">
      <div className="flex items-center justify-between mb-4">
        {title && <h2 className="text-lg font-semibold">{title}</h2>}
        <div className="flex items-center gap-2">
          {manualCount > 0 && (
            <div className="flex items-center rounded border border-subtle text-xs overflow-hidden">
              {(['all', 'signal', 'manual'] as const).map((v) => (
                <button
                  key={v}
                  className={`px-2.5 py-1 transition-colors ${sourceFilter === v ? 'bg-hover text-primary font-medium' : 'text-secondary hover:text-primary'}`}
                  onClick={() => setSourceFilter(v)}
                >
                  {v === 'all' ? 'All' : v === 'signal' ? `Signal (${signalCount})` : `Manual (${manualCount})`}
                </button>
              ))}
            </div>
          )}
          {cancelledCount > 0 && (
            <button
              className="text-xs px-2.5 py-1 rounded border border-subtle text-secondary hover:text-primary hover:border-muted transition-colors"
              onClick={() => setHideCancelled(!hideCancelled)}
            >
              {hideCancelled ? `Show ${cancelledCount} cancelled` : 'Hide cancelled'}
            </button>
          )}
        </div>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-secondary border-b border-subtle">
            <th className="text-left pb-3 pr-4">#</th>
            <th className="text-left pb-3 pr-4">Ticker</th>
            <th className="text-left pb-3 pr-4">Direction</th>
            <th className="text-left pb-3 pr-4">Strike</th>
            <th className="text-right pb-3 pr-4">Alert Price</th>
            <th className="text-right pb-3 pr-4">Entry</th>
            <th className="text-right pb-3 pr-4">Best Entry</th>
            <th className="text-right pb-3 pr-4">Exit</th>
            <th className="text-right pb-3 pr-4">Max</th>
            <th className="text-right pb-3 pr-4">P&L ($)</th>
            <th className="text-right pb-3 pr-4">P&L (%)</th>
            {!compact && <th className="text-left pb-3 pr-4">Exit Reason</th>}
            <th className="text-left pb-3 pr-4">Status</th>
            <th className="text-left pb-3 pr-4">Opened</th>
            <th className="text-left pb-3">Closed</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((trade) => (
            <>
              <tr
                key={trade.id}
                className="border-b border-row hover:bg-hover cursor-pointer"
                onClick={() => setExpandedId(expandedId === trade.id ? null : trade.id)}
              >
                <td className="py-2 pr-4">{trade.id}</td>
                <td className="py-2 pr-4 font-medium">{trade.ticker || '-'}</td>
                <td className={`py-2 pr-4 font-semibold ${trade.direction === 'CALL' ? 'text-green-400' : 'text-red-400'}`}>
                  {trade.direction}
                </td>
                <td className="py-2 pr-4">${trade.strike_price.toFixed(0)}</td>
                <td className="py-2 pr-4 text-right text-secondary">
                  {trade.alert_option_price != null ? `$${trade.alert_option_price.toFixed(2)}` : '-'}
                </td>
                <td className="py-2 pr-4 text-right">
                  {trade.entry_price != null ? `$${trade.entry_price.toFixed(2)}` : '-'}
                </td>
                <td className="py-2 pr-4 text-right text-secondary">
                  {trade.best_entry_price != null
                    ? `$${trade.best_entry_price.toFixed(2)}${trade.best_entry_minutes != null ? ` (${Math.round(trade.best_entry_minutes)}m)` : ''}`
                    : '-'}
                </td>
                <td className="py-2 pr-4 text-right">
                  {trade.exit_price != null ? `$${trade.exit_price.toFixed(2)}` : '-'}
                </td>
                <td className="py-2 pr-4 text-right text-secondary">
                  {trade.highest_price_seen != null ? `$${trade.highest_price_seen.toFixed(2)}` : '-'}
                </td>
                <td className={`py-2 pr-4 text-right font-semibold ${pnlColor(trade.pnl_dollars)}`}>
                  {trade.pnl_dollars != null ? formatCurrency(trade.pnl_dollars) : '-'}
                </td>
                <td className={`py-2 pr-4 text-right ${pnlColor(trade.pnl_percent)}`}>
                  {trade.pnl_percent != null ? formatPercent(trade.pnl_percent) : '-'}
                </td>
                {!compact && (
                  <td className="py-2 pr-4 text-xs">
                    {trade.exit_reason?.replace(/_/g, ' ') || '-'}
                  </td>
                )}
                <td className={`py-2 pr-4 text-xs font-medium ${statusColor(trade.status)}`}>
                  {trade.status}
                </td>
                <td className="py-2 pr-4 text-xs text-secondary">
                  {trade.entry_filled_at ? formatTime(trade.entry_filled_at) : '-'}
                </td>
                <td className="py-2 text-xs text-secondary">
                  {trade.exit_filled_at ? formatTime(trade.exit_filled_at) : '-'}
                </td>
              </tr>
              {expandedId === trade.id && (
                <tr key={`${trade.id}-events`} className="border-b border-row">
                  <td colSpan={compact ? COL_COUNT - 1 : COL_COUNT}>
                    <div className="bg-inset rounded m-1">
                      <TradeActivityLog tradeId={trade.id} />
                      {['CLOSED', 'CANCELLED'].includes(trade.status) && (
                        <div className="px-6 pb-3 flex items-center gap-3">
                          <button
                            className="text-xs px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-500 text-white font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                            disabled={retaking === trade.id}
                            onClick={(e) => {
                              e.stopPropagation()
                              setRetaking(trade.id)
                              setRetakeMsg(null)
                              retakeTrade(trade.id)
                                .then((res) => {
                                  setRetakeMsg(res.message)
                                  if (res.status === 'accepted' && onRetake) onRetake()
                                })
                                .catch((err) => {
                                  setRetakeMsg(err.response?.data?.detail || 'Retake failed')
                                })
                                .finally(() => setRetaking(null))
                            }}
                          >
                            {retaking === trade.id ? 'Placing...' : `Retake ${trade.direction}`}
                          </button>
                          {retakeMsg && (
                            <span className="text-xs text-secondary">{retakeMsg}</span>
                          )}
                        </div>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </div>
  )
}
