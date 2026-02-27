import { useState } from 'react'
import type { Alert } from '../types'
import { formatTime } from '../utils/format'

interface Props {
  alerts: Alert[]
}

function alertStatusColor(status: string): string {
  switch (status) {
    case 'RECEIVED': return 'text-blue-400'
    case 'ACCEPTED': return 'text-green-400'
    case 'PROCESSED': return 'text-green-400'
    case 'REJECTED': return 'text-red-400'
    case 'ERROR': return 'text-red-400'
    default: return 'text-gray-400'
  }
}

function formatPayload(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

export function AlertTable({ alerts }: Props) {
  const [expandedId, setExpandedId] = useState<number | null>(null)

  if (alerts.length === 0) {
    return (
      <div className="bg-surface rounded-lg p-6">
        <p className="text-muted text-center py-8">No alerts to display</p>
      </div>
    )
  }

  return (
    <div className="bg-surface rounded-lg p-6 overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-secondary border-b border-subtle">
            <th className="text-left pb-3 pr-4">#</th>
            <th className="text-left pb-3 pr-4">Time</th>
            <th className="text-left pb-3 pr-4">Source</th>
            <th className="text-left pb-3 pr-4">Ticker</th>
            <th className="text-left pb-3 pr-4">Direction</th>
            <th className="text-right pb-3 pr-4">Signal Price</th>
            <th className="text-left pb-3 pr-4">Status</th>
            <th className="text-left pb-3 pr-4">Reason</th>
            <th className="text-left pb-3">Trade</th>
          </tr>
        </thead>
        <tbody>
          {alerts.map((alert) => (
            <>
              <tr
                key={alert.id}
                className="border-b border-row hover:bg-hover cursor-pointer"
                onClick={() => setExpandedId(expandedId === alert.id ? null : alert.id)}
              >
                <td className="py-2 pr-4">{alert.id}</td>
                <td className="py-2 pr-4 text-secondary">
                  {formatTime(alert.received_at)}
                </td>
                <td className="py-2 pr-4">
                  <span className={`text-xs px-1.5 py-0.5 rounded ${
                    alert.source === 'test'
                      ? 'bg-purple-900/50 text-purple-400'
                      : alert.source === 'strategy_signal'
                        ? 'bg-emerald-900/50 text-emerald-400'
                        : alert.source === 'orb_auto'
                          ? 'bg-amber-900/50 text-amber-400'
                          : 'bg-sky-900/50 text-sky-400'
                  }`}>
                    {alert.source === 'test' ? 'Test'
                      : alert.source === 'strategy_signal' ? 'Strategy'
                      : alert.source === 'orb_auto' ? 'ORB Auto'
                      : alert.source === 'retake' ? 'Retake'
                      : 'TradingView'}
                  </span>
                </td>
                <td className="py-2 pr-4 font-mono">{alert.ticker}</td>
                <td className={`py-2 pr-4 font-semibold ${
                  alert.direction === 'CALL' ? 'text-green-400'
                    : alert.direction === 'PUT' ? 'text-red-400'
                    : 'text-secondary'
                }`}>
                  {alert.direction ?? 'CLOSE'}
                </td>
                <td className="py-2 pr-4 text-right">
                  {alert.signal_price != null ? `$${alert.signal_price.toFixed(2)}` : '-'}
                </td>
                <td className={`py-2 pr-4 text-xs font-medium ${alertStatusColor(alert.status)}`}>
                  {alert.status}
                </td>
                <td className="py-2 pr-4 text-xs text-secondary">
                  {alert.rejection_reason || '-'}
                </td>
                <td className="py-2 text-xs">
                  {alert.trade_id != null ? `#${alert.trade_id}` : '-'}
                </td>
              </tr>
              {expandedId === alert.id && alert.raw_payload && (
                <tr key={`${alert.id}-payload`} className="border-b border-row">
                  <td colSpan={9} className="py-3 px-4">
                    <div className="bg-deep rounded p-3">
                      <p className="text-[11px] uppercase tracking-wide text-muted mb-2">Raw Payload</p>
                      <pre className="text-xs text-tertiary font-mono whitespace-pre-wrap break-all">
                        {formatPayload(alert.raw_payload)}
                      </pre>
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
