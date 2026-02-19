import { useEffect, useState } from 'react'
import { TradeTable } from '../components/TradeTable'
import { fetchTrades, fetchTickers } from '../api/trades'
import type { Trade } from '../types'

export default function TradeHistory() {
  const [trades, setTrades] = useState<Trade[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [dateFilter, setDateFilter] = useState<string>('')
  const [tickerFilter, setTickerFilter] = useState<string>('')
  const [tickers, setTickers] = useState<string[]>([])
  const perPage = 25

  useEffect(() => {
    fetchTickers().then(setTickers).catch(() => {})
  }, [])

  useEffect(() => {
    const params: Record<string, string | number> = { page, per_page: perPage }
    if (statusFilter) params.status = statusFilter
    if (dateFilter) params.trade_date = dateFilter
    if (tickerFilter) params.ticker = tickerFilter

    fetchTrades(params as any)
      .then((d) => {
        setTrades(d.trades)
        setTotal(d.total)
      })
      .catch(() => {})
  }, [page, statusFilter, dateFilter, tickerFilter])

  const totalPages = Math.ceil(total / perPage)

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-2xl font-bold">Trade History</h1>

      <div className="flex flex-wrap gap-4">
        <div>
          <label className="block text-sm text-secondary mb-1">Date</label>
          <input
            type="date"
            value={dateFilter}
            onChange={(e) => { setDateFilter(e.target.value); setPage(1) }}
            className="bg-surface border border-default rounded px-3 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="block text-sm text-secondary mb-1">Ticker</label>
          <select
            value={tickerFilter}
            onChange={(e) => { setTickerFilter(e.target.value); setPage(1) }}
            className="bg-surface border border-default rounded px-3 py-1.5 text-sm"
          >
            <option value="">All</option>
            {tickers.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm text-secondary mb-1">Status</label>
          <select
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
            className="bg-surface border border-default rounded px-3 py-1.5 text-sm"
          >
            <option value="">All</option>
            <option value="CLOSED">Closed</option>
            <option value="PENDING">Pending</option>
            <option value="FILLED">Filled</option>
            <option value="STOP_LOSS_PLACED">Stop Loss Placed</option>
            <option value="EXITING">Exiting</option>
            <option value="CANCELLED">Cancelled</option>
          </select>
        </div>
        {(dateFilter || statusFilter || tickerFilter) && (
          <button
            onClick={() => { setDateFilter(''); setStatusFilter(''); setTickerFilter(''); setPage(1) }}
            className="self-end bg-elevated hover:bg-elevated rounded px-3 py-1.5 text-sm"
          >
            Clear Filters
          </button>
        )}
      </div>

      <TradeTable trades={trades} />

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-secondary">
            Showing {(page - 1) * perPage + 1}-{Math.min(page * perPage, total)} of {total}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="bg-elevated hover:bg-elevated disabled:opacity-50 rounded px-3 py-1 text-sm"
            >
              Previous
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="bg-elevated hover:bg-elevated disabled:opacity-50 rounded px-3 py-1 text-sm"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
