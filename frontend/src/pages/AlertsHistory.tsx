import { useEffect, useState } from 'react'
import { AlertTable } from '../components/AlertTable'
import { fetchAlerts } from '../api/alerts'
import type { Alert } from '../types'

export default function AlertsHistory() {
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [dateFilter, setDateFilter] = useState<string>('')
  const [tradingWindowOnly, setTradingWindowOnly] = useState(true)
  const perPage = 25

  useEffect(() => {
    const params: Record<string, string | number | boolean> = { page, per_page: perPage }
    if (statusFilter) params.status = statusFilter
    if (dateFilter) params.alert_date = dateFilter
    if (tradingWindowOnly) params.trading_window_only = true

    fetchAlerts(params as any)
      .then((d) => {
        setAlerts(d.alerts)
        setTotal(d.total)
      })
      .catch(() => {})
  }, [page, statusFilter, dateFilter, tradingWindowOnly])

  const totalPages = Math.ceil(total / perPage)

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-2xl font-bold">Signals</h1>

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
          <label className="block text-sm text-secondary mb-1">Status</label>
          <select
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
            className="bg-surface border border-default rounded px-3 py-1.5 text-sm"
          >
            <option value="">All</option>
            <option value="RECEIVED">Received</option>
            <option value="ACCEPTED">Accepted</option>
            <option value="PROCESSED">Processed</option>
            <option value="REJECTED">Rejected</option>
            <option value="ERROR">Error</option>
          </select>
        </div>
        <label className="self-end flex items-center gap-2 cursor-pointer py-1.5">
          <input
            type="checkbox"
            checked={tradingWindowOnly}
            onChange={(e) => { setTradingWindowOnly(e.target.checked); setPage(1) }}
            className="accent-blue-500"
          />
          <span className="text-sm text-tertiary">Trading window only</span>
        </label>
        {(dateFilter || statusFilter || tradingWindowOnly) && (
          <button
            onClick={() => { setDateFilter(''); setStatusFilter(''); setTradingWindowOnly(false); setPage(1) }}
            className="self-end bg-elevated hover:bg-elevated rounded px-3 py-1.5 text-sm"
          >
            Clear Filters
          </button>
        )}
      </div>

      <AlertTable alerts={alerts} />

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
