import { useEffect, useState } from 'react'
import { useWebSocket } from '../hooks/useWebSocket'
import { fetchTrades } from '../api/trades'
import { sendTestAlert, testCloseTrade } from '../api/testing'
import { formatCurrency, statusColor } from '../utils/format'
import type { Trade } from '../types'

export default function Testing() {
  // Send Alert
  const [alertPrice, setAlertPrice] = useState('')
  const [webhookSecret, setWebhookSecret] = useState('')
  const [alertResult, setAlertResult] = useState<{
    status: string
    message: string
  } | null>(null)
  const [alertLoading, setAlertLoading] = useState(false)

  // Open Trades
  const [openTrades, setOpenTrades] = useState<Trade[]>([])
  const [closePercents, setClosePercents] = useState<Record<number, string>>({})
  const [closeLoading, setCloseLoading] = useState<Record<number, boolean>>({})
  const [closeResults, setCloseResults] = useState<
    Record<number, { status: string; message: string }>
  >({})

  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const { lastMessage } = useWebSocket(
    `${wsProtocol}://${window.location.host}/ws/dashboard`,
  )

  const loadOpenTrades = () => {
    fetchTrades({ per_page: 50 })
      .then((d) => {
        setOpenTrades(
          d.trades.filter((t) =>
            ['FILLED', 'STOP_LOSS_PLACED', 'EXITING', 'PENDING'].includes(
              t.status,
            ),
          ),
        )
      })
      .catch(() => {})
  }

  useEffect(() => {
    loadOpenTrades()
    const interval = setInterval(loadOpenTrades, 15000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (!lastMessage) return
    if (
      [
        'trade_created',
        'trade_filled',
        'trade_closed',
        'trade_cancelled',
      ].includes(lastMessage.event)
    ) {
      loadOpenTrades()
    }
  }, [lastMessage])

  const handleSendAlert = async (action: 'BUY_CALL' | 'BUY_PUT') => {
    if (!webhookSecret.trim()) {
      setAlertResult({ status: 'error', message: 'Webhook secret is required' })
      return
    }
    setAlertLoading(true)
    setAlertResult(null)
    try {
      const payload: {
        ticker: string
        action: 'BUY_CALL' | 'BUY_PUT'
        secret: string
        price?: number
      } = {
        ticker: 'SPY',
        action,
        secret: webhookSecret,
      }
      if (alertPrice.trim()) {
        payload.price = parseFloat(alertPrice)
      }
      const result = await sendTestAlert(payload)
      setAlertResult(result)
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ||
        (err as Error)?.message ||
        'Failed to send alert'
      setAlertResult({ status: 'error', message })
    } finally {
      setAlertLoading(false)
    }
  }

  const handleCloseTrade = async (tradeId: number) => {
    const percentStr = closePercents[tradeId]
    if (!percentStr || isNaN(parseFloat(percentStr))) {
      setCloseResults((prev) => ({
        ...prev,
        [tradeId]: { status: 'error', message: 'Enter a valid percentage' },
      }))
      return
    }
    setCloseLoading((prev) => ({ ...prev, [tradeId]: true }))
    setCloseResults((prev) => {
      const next = { ...prev }
      delete next[tradeId]
      return next
    })
    try {
      const result = await testCloseTrade(tradeId, parseFloat(percentStr))
      setCloseResults((prev) => ({ ...prev, [tradeId]: result }))
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ||
        (err as Error)?.message ||
        'Failed to close trade'
      setCloseResults((prev) => ({
        ...prev,
        [tradeId]: { status: 'error', message },
      }))
    } finally {
      setCloseLoading((prev) => ({ ...prev, [tradeId]: false }))
    }
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-2xl font-bold">Testing</h1>

      {/* Send Alert */}
      <div className="bg-slate-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold mb-4">Send Alert</h2>
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1">
              Webhook Secret
            </label>
            <input
              type="password"
              value={webhookSecret}
              onChange={(e) => setWebhookSecret(e.target.value)}
              placeholder="Enter secret"
              className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm w-48 focus:outline-none focus:border-slate-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">
              Price (optional)
            </label>
            <input
              type="number"
              step="0.01"
              value={alertPrice}
              onChange={(e) => setAlertPrice(e.target.value)}
              placeholder="e.g. 600.00"
              className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm w-32 focus:outline-none focus:border-slate-500"
            />
          </div>
          <button
            onClick={() => handleSendAlert('BUY_CALL')}
            disabled={alertLoading}
            className="bg-green-600 hover:bg-green-500 disabled:opacity-50 rounded px-4 py-1.5 text-sm font-semibold transition-colors"
          >
            {alertLoading ? 'Sending...' : 'BUY CALL'}
          </button>
          <button
            onClick={() => handleSendAlert('BUY_PUT')}
            disabled={alertLoading}
            className="bg-red-600 hover:bg-red-500 disabled:opacity-50 rounded px-4 py-1.5 text-sm font-semibold transition-colors"
          >
            {alertLoading ? 'Sending...' : 'BUY PUT'}
          </button>
        </div>
        {alertResult && (
          <div
            className={`mt-3 text-sm px-3 py-2 rounded ${
              alertResult.status === 'error' ||
              alertResult.status === 'rejected'
                ? 'bg-red-900/30 text-red-400'
                : 'bg-green-900/30 text-green-400'
            }`}
          >
            {alertResult.message}
          </div>
        )}
      </div>

      {/* Open Trades */}
      <div className="bg-slate-800 rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Open Trades</h2>
          {openTrades.length > 0 && (
            <span className="text-xs font-medium text-yellow-400 bg-yellow-400/10 px-2 py-0.5 rounded-full">
              {openTrades.length} open
            </span>
          )}
        </div>

        {openTrades.length === 0 ? (
          <p className="text-slate-500 text-center py-8">
            No open trades. Send an alert above to create one.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-slate-400 border-b border-slate-700">
                  <th className="text-left pb-3 pr-4">#</th>
                  <th className="text-left pb-3 pr-4">Direction</th>
                  <th className="text-left pb-3 pr-4">Strike</th>
                  <th className="text-right pb-3 pr-4">Entry</th>
                  <th className="text-left pb-3 pr-4">Status</th>
                  <th className="text-right pb-3 pr-4">P&L %</th>
                  <th className="text-left pb-3">Action</th>
                </tr>
              </thead>
              <tbody>
                {openTrades.map((trade) => (
                  <tr
                    key={trade.id}
                    className="border-b border-slate-700/50"
                  >
                    <td className="py-2.5 pr-4 text-slate-400">{trade.id}</td>
                    <td className="py-2.5 pr-4">
                      <span
                        className={`font-semibold ${
                          trade.direction === 'CALL'
                            ? 'text-green-400'
                            : 'text-red-400'
                        }`}
                      >
                        {trade.direction}
                      </span>
                    </td>
                    <td className="py-2.5 pr-4">
                      ${trade.strike_price.toFixed(0)}
                    </td>
                    <td className="py-2.5 pr-4 text-right">
                      {trade.entry_price != null
                        ? formatCurrency(trade.entry_price)
                        : '\u2014'}
                    </td>
                    <td
                      className={`py-2.5 pr-4 text-xs font-medium ${statusColor(trade.status)}`}
                    >
                      {trade.status}
                    </td>
                    <td className="py-2.5 pr-4 text-right">
                      <input
                        type="number"
                        step="1"
                        value={closePercents[trade.id] || ''}
                        onChange={(e) =>
                          setClosePercents((prev) => ({
                            ...prev,
                            [trade.id]: e.target.value,
                          }))
                        }
                        placeholder="e.g. 10 or -20"
                        className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm w-28 text-right focus:outline-none focus:border-slate-500"
                      />
                    </td>
                    <td className="py-2.5">
                      <button
                        onClick={() => handleCloseTrade(trade.id)}
                        disabled={
                          closeLoading[trade.id] || trade.entry_price == null
                        }
                        className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 rounded px-3 py-1 text-xs font-semibold transition-colors"
                      >
                        {closeLoading[trade.id] ? 'Closing...' : 'Close'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {/* Per-trade result messages */}
            {openTrades.map((trade) =>
              closeResults[trade.id] ? (
                <div
                  key={`result-${trade.id}`}
                  className={`mt-2 text-xs px-3 py-1.5 rounded ${
                    closeResults[trade.id].status === 'error'
                      ? 'bg-red-900/30 text-red-400'
                      : 'bg-green-900/30 text-green-400'
                  }`}
                >
                  Trade #{trade.id}: {closeResults[trade.id].message}
                </div>
              ) : null,
            )}
          </div>
        )}
      </div>
    </div>
  )
}
