export type TradeDirection = 'CALL' | 'PUT'

export type TradeStatus =
  | 'PENDING'
  | 'FILLED'
  | 'STOP_LOSS_PLACED'
  | 'EXITING'
  | 'CLOSED'
  | 'CANCELLED'
  | 'ERROR'

export type ExitReason =
  | 'STOP_LOSS'
  | 'TRAILING_STOP'
  | 'PROFIT_TARGET'
  | 'MAX_HOLD_TIME'
  | 'TIME_BASED'
  | 'MANUAL'
  | 'EXPIRY'

export interface Trade {
  id: number
  trade_date: string
  direction: TradeDirection
  option_symbol: string
  strike_price: number
  entry_price: number | null
  entry_quantity: number
  entry_filled_at: string | null
  alert_option_price: number | null
  exit_price: number | null
  exit_filled_at: string | null
  exit_reason: ExitReason | null
  stop_loss_price: number | null
  trailing_stop_price: number | null
  highest_price_seen: number | null
  pnl_dollars: number | null
  pnl_percent: number | null
  status: TradeStatus
  source: string | null
  created_at: string
  best_entry_price: number | null
  best_entry_minutes: number | null
  ticker: string | null
}

export interface DailyStats {
  trade_date: string
  total_trades: number
  trades_remaining: number
  winning_trades: number
  losing_trades: number
  total_pnl: number
  win_rate: number
  open_positions: number
}

export interface PnLDataPoint {
  timestamp: string
  cumulative_pnl: number
  trade_id: number
}

export interface PnLChartData {
  data_points: PnLDataPoint[]
  total_pnl: number
}

export interface PnLSummaryDay {
  trade_date: string
  pnl: number
  total_trades: number
  winning_trades: number
  losing_trades: number
}

export interface PnLSummaryData {
  period: string
  days: PnLSummaryDay[]
  total_pnl: number
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
}

export interface TradeListResponse {
  trades: Trade[]
  total: number
  page: number
  per_page: number
}

export type AlertStatus = 'RECEIVED' | 'ACCEPTED' | 'REJECTED' | 'PROCESSED' | 'ERROR'

export interface Alert {
  id: number
  received_at: string
  ticker: string
  direction: TradeDirection | null
  signal_price: number | null
  source: string | null
  status: AlertStatus
  rejection_reason: string | null
  trade_id: number | null
  raw_payload: string | null
}

export interface AlertListResponse {
  alerts: Alert[]
  total: number
  page: number
  per_page: number
}

export type TradeEventType =
  | 'ALERT_RECEIVED'
  | 'CONTRACT_SELECTED'
  | 'ENTRY_ORDER_PLACED'
  | 'ENTRY_FILLED'
  | 'ENTRY_CANCELLED'
  | 'STOP_LOSS_PLACED'
  | 'STOP_LOSS_CANCELLED'
  | 'EXIT_TRIGGERED'
  | 'EXIT_ORDER_PLACED'
  | 'EXIT_FILLED'
  | 'STOP_LOSS_HIT'
  | 'CLOSE_SIGNAL'
  | 'MANUAL_CLOSE'

export interface TradeEvent {
  id: number
  trade_id: number
  timestamp: string
  event_type: TradeEventType
  message: string
  details: Record<string, unknown> | null
}

export interface TradeEventListResponse {
  events: TradeEvent[]
  trade_id: number
}

export interface PriceSnapshot {
  timestamp: string
  price: number
  highest_price_seen: number
}

export interface PriceSnapshotListResponse {
  snapshots: PriceSnapshot[]
  trade_id: number
  entry_price: number | null
  stop_loss_price: number | null
}

export interface QuoteItem {
  trade_id: number
  option_symbol: string
  last_price: number | null
  bid: number | null
  ask: number | null
}

export interface QuotesResponse {
  quotes: QuoteItem[]
}

export interface WSMessage {
  event: string
  data: Record<string, unknown>
}
