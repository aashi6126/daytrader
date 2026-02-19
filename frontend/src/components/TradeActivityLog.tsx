import { useEffect, useState } from 'react'
import type { TradeEvent } from '../types'
import { fetchTradeEvents } from '../api/trades'
import { TradePriceChart } from './TradePriceChart'

interface Props {
  tradeId: number
}

const EVENT_COLORS: Record<string, string> = {
  ALERT_RECEIVED: 'bg-blue-500',
  CONTRACT_SELECTED: 'bg-blue-400',
  ENTRY_ORDER_PLACED: 'bg-yellow-500',
  ENTRY_FILLED: 'bg-green-500',
  ENTRY_CANCELLED: 'bg-red-500',
  STOP_LOSS_PLACED: 'bg-orange-500',
  STOP_LOSS_CANCELLED: 'bg-orange-400',
  EXIT_TRIGGERED: 'bg-purple-500',
  EXIT_ORDER_PLACED: 'bg-yellow-400',
  EXIT_FILLED: 'bg-green-400',
  STOP_LOSS_HIT: 'bg-red-400',
  CLOSE_SIGNAL: 'bg-purple-400',
  MANUAL_CLOSE: 'bg-pink-500',
}

function formatEventTime(timestamp: string): string {
  const d = new Date(timestamp.endsWith('Z') || timestamp.includes('+') ? timestamp : timestamp + 'Z')
  return d.toLocaleString('en-US', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  })
}

function formatEventType(type: string): string {
  return type.replace(/_/g, ' ')
}

export function TradeActivityLog({ tradeId }: Props) {
  const [events, setEvents] = useState<TradeEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedEvent, setExpandedEvent] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchTradeEvents(tradeId)
      .then((res) => {
        if (!cancelled) setEvents(res.events)
      })
      .catch(() => {
        if (!cancelled) setEvents([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [tradeId])

  if (loading) {
    return (
      <div className="py-4 px-6 text-muted text-sm">Loading events...</div>
    )
  }

  if (events.length === 0) {
    return (
      <div className="py-4 px-6 text-muted text-sm">No events recorded</div>
    )
  }

  return (
    <div className="py-3 px-6">
      <TradePriceChart tradeId={tradeId} />
      <p className="text-[11px] uppercase tracking-wide text-muted mb-3 mt-4">Activity Log</p>
      <div className="relative ml-2">
        {/* Vertical line */}
        <div className="absolute left-[5px] top-2 bottom-2 w-px bg-elevated" />

        {events.map((event) => (
          <div key={event.id} className="relative pl-6 pb-3 last:pb-0">
            {/* Dot */}
            <div
              className={`absolute left-0 top-[6px] w-[11px] h-[11px] rounded-full border-2 border-deep ${EVENT_COLORS[event.event_type] || 'bg-gray-500'}`}
            />

            <div className="flex items-baseline gap-3">
              <span className="text-[11px] text-muted font-mono whitespace-nowrap">
                {formatEventTime(event.timestamp)}
              </span>
              <span className={`text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded ${EVENT_COLORS[event.event_type] || 'bg-gray-500'} bg-opacity-20 text-tertiary`}>
                {formatEventType(event.event_type)}
              </span>
            </div>

            <p className="text-sm text-tertiary mt-0.5">{event.message}</p>

            {event.details && (
              <button
                className="text-[11px] text-muted hover:text-secondary mt-0.5"
                onClick={(e) => {
                  e.stopPropagation()
                  setExpandedEvent(expandedEvent === event.id ? null : event.id)
                }}
              >
                {expandedEvent === event.id ? 'hide details' : 'show details'}
              </button>
            )}

            {expandedEvent === event.id && event.details && (
              <pre className="text-[11px] text-secondary font-mono mt-1 bg-inset rounded p-2 whitespace-pre-wrap break-all">
                {JSON.stringify(event.details, null, 2)}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
