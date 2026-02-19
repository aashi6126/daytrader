import { useEffect, useState } from 'react'
import type { Trade, QuoteItem } from '../types'
import { formatCurrency, formatTime } from '../utils/format'
import { fetchOpenQuotes, closeTrade, cancelTrade } from '../api/trades'

interface Props {
  trades: Trade[]
  onClose?: () => void
}

const statusLabel: Record<string, string> = {
  PENDING: 'Pending Fill',
  FILLED: 'Active',
  STOP_LOSS_PLACED: 'Active',
  EXITING: 'Closing',
}

const statusDot: Record<string, string> = {
  PENDING: 'bg-yellow-400',
  FILLED: 'bg-green-400',
  STOP_LOSS_PLACED: 'bg-green-400',
  EXITING: 'bg-orange-400',
}

export function OpenPositions({ trades, onClose }: Props) {
  const [quotes, setQuotes] = useState<Record<number, QuoteItem>>({})
  const [closing, setClosing] = useState<number | null>(null)

  useEffect(() => {
    if (trades.length === 0) return

    const load = () => {
      fetchOpenQuotes()
        .then((res) => {
          const map: Record<number, QuoteItem> = {}
          for (const q of res.quotes) map[q.trade_id] = q
          setQuotes(map)
        })
        .catch(() => {})
    }

    load()
    const interval = setInterval(load, 5000)
    return () => clearInterval(interval)
  }, [trades.length])

  if (trades.length === 0) {
    return (
      <div className="bg-surface rounded-lg p-6">
        <h2 className="text-lg font-semibold mb-4">Open Positions</h2>
        <p className="text-muted text-center py-8">No open positions</p>
      </div>
    )
  }

  return (
    <div className="bg-surface rounded-lg p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">Open Positions</h2>
        <span className="text-xs font-medium text-yellow-400 bg-yellow-400/10 px-2 py-0.5 rounded-full">
          {trades.length} open
        </span>
      </div>
      <div className="space-y-3">
        {trades.map((trade) => {
          const isCall = trade.direction === 'CALL'
          const quote = quotes[trade.id]
          const lastPrice = quote?.last_price ?? null
          const unrealizedPnl =
            lastPrice != null && trade.entry_price != null
              ? (lastPrice - trade.entry_price) * trade.entry_quantity * 100
              : null
          const unrealizedPct =
            lastPrice != null && trade.entry_price != null && trade.entry_price > 0
              ? ((lastPrice - trade.entry_price) / trade.entry_price) * 100
              : null

          return (
            <div
              key={trade.id}
              className="rounded-lg border border-subtle bg-hover overflow-hidden"
            >
              {/* Header row */}
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-row">
                <div className="flex items-center gap-2.5">
                  <span
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-bold ${
                      isCall
                        ? 'bg-green-900/50 text-green-400'
                        : 'bg-red-900/50 text-red-400'
                    }`}
                  >
                    {isCall ? '\u25B2' : '\u25BC'} {trade.direction}
                  </span>
                  <span className="font-semibold text-sm">
                    ${trade.strike_price.toFixed(0)}
                  </span>
                  <span className="text-muted text-xs">#{trade.id}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className={`w-1.5 h-1.5 rounded-full ${statusDot[trade.status] ?? 'bg-gray-400'}`} />
                  <span className="text-xs text-tertiary">
                    {statusLabel[trade.status] ?? trade.status}
                  </span>
                </div>
              </div>

              {/* Details */}
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 px-4 py-3 text-sm">
                {trade.status === 'PENDING' ? (
                  <>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Limit Price</p>
                      <p className="font-medium text-yellow-400">
                        {trade.alert_option_price != null
                          ? formatCurrency(trade.alert_option_price * 0.95)
                          : '\u2014'}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Current Price</p>
                      <p className="font-medium text-primary">
                        {lastPrice != null ? formatCurrency(lastPrice) : '\u2014'}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Mid at Signal</p>
                      <p className="font-medium text-tertiary">
                        {trade.alert_option_price != null ? formatCurrency(trade.alert_option_price) : '\u2014'}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Qty</p>
                      <p className="font-medium text-primary">
                        {trade.entry_quantity}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Bid / Ask</p>
                      <p className="font-medium text-tertiary">
                        {quote?.bid != null && quote?.ask != null
                          ? `${formatCurrency(quote.bid)} / ${formatCurrency(quote.ask)}`
                          : '\u2014'}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Submitted</p>
                      <p className="font-medium text-tertiary">
                        {formatTime(trade.created_at)}
                      </p>
                    </div>
                  </>
                ) : (
                  <>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Entry</p>
                      <p className="font-medium text-primary">
                        {trade.entry_price != null ? formatCurrency(trade.entry_price) : '\u2014'}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Max Price</p>
                      <p className="font-medium text-primary">
                        {trade.highest_price_seen != null ? formatCurrency(trade.highest_price_seen) : '\u2014'}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Last Price</p>
                      <p className="font-medium text-primary">
                        {lastPrice != null ? formatCurrency(lastPrice) : '\u2014'}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Unrealized P&L</p>
                      <p className={`font-semibold ${
                        unrealizedPnl == null ? 'text-secondary'
                          : unrealizedPnl >= 0 ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {unrealizedPnl != null
                          ? `${formatCurrency(unrealizedPnl)} (${unrealizedPct! >= 0 ? '+' : ''}${unrealizedPct!.toFixed(1)}%)`
                          : '\u2014'}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Stop Loss</p>
                      <p className="font-medium text-red-400/80">
                        {trade.stop_loss_price != null ? formatCurrency(trade.stop_loss_price) : '\u2014'}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Trail Stop</p>
                      <p className="font-medium text-orange-400/80">
                        {trade.trailing_stop_price != null ? formatCurrency(trade.trailing_stop_price) : '\u2014'}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Qty</p>
                      <p className="font-medium text-primary">
                        {trade.entry_quantity}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wide text-muted">Opened</p>
                      <p className="font-medium text-tertiary">
                        {trade.entry_filled_at ? formatTime(trade.entry_filled_at) : '\u2014'}
                      </p>
                    </div>
                  </>
                )}
              </div>

              {/* Close button */}
              {['FILLED', 'STOP_LOSS_PLACED'].includes(trade.status) && (
                <div className="px-4 pb-3">
                  <button
                    className="w-full text-xs px-3 py-1.5 rounded bg-red-600/20 hover:bg-red-600/40 text-red-400 font-medium border border-red-600/30 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    disabled={closing === trade.id}
                    onClick={() => {
                      setClosing(trade.id)
                      closeTrade(trade.id)
                        .then(() => { if (onClose) onClose() })
                        .catch(() => {})
                        .finally(() => setClosing(null))
                    }}
                  >
                    {closing === trade.id ? 'Closing...' : 'Close Now'}
                  </button>
                </div>
              )}
              {/* Cancel pending entry */}
              {trade.status === 'PENDING' && (
                <div className="px-4 pb-3">
                  <button
                    className="w-full text-xs px-3 py-1.5 rounded bg-yellow-600/20 hover:bg-yellow-600/40 text-yellow-400 font-medium border border-yellow-600/30 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    disabled={closing === trade.id}
                    onClick={() => {
                      setClosing(trade.id)
                      cancelTrade(trade.id)
                        .then(() => { if (onClose) onClose() })
                        .catch(() => {})
                        .finally(() => setClosing(null))
                    }}
                  >
                    {closing === trade.id ? 'Cancelling...' : 'Cancel Entry'}
                  </button>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
