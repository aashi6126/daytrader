import api from './client'

export interface TickerInfo {
  ticker: string
  timeframes: string[]
}

export interface StockBacktestParams {
  ticker: string
  start_date: string
  end_date: string
  signal_type: string
  ema_fast: number
  ema_slow: number
  bar_interval: string
  rsi_period: number
  rsi_ob: number
  rsi_os: number
  orb_minutes: number
  atr_period: number
  atr_stop_mult: number
  afternoon_enabled: boolean
  quantity: number
  stop_loss_percent: number
  profit_target_percent: number
  trailing_stop_percent: number
  max_hold_minutes: number
  min_confluence: number
  vol_threshold: number
  orb_body_min_pct: number
  orb_vwap_filter: boolean
  orb_gap_fade_filter: boolean
  orb_stop_mult: number
  orb_target_mult: number
  max_daily_trades: number
  max_daily_loss: number
  max_consecutive_losses: number
}

export interface StockTrade {
  trade_date: string
  ticker: string
  direction: 'CALL' | 'PUT'
  strike: number
  entry_time: string
  entry_price: number
  exit_time: string | null
  exit_price: number | null
  exit_reason: string | null
  highest_price_seen: number
  pnl_dollars: number | null
  pnl_percent: number | null
  hold_minutes: number | null
  quantity: number
  underlying_price: number | null
  expiry_date: string | null
  dte: number
  delta: number | null
}

export interface StockDay {
  trade_date: string
  pnl: number
  total_trades: number
  winning_trades: number
  losing_trades: number
}

export interface StockSummary {
  total_pnl: number
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  avg_win: number
  avg_loss: number
  largest_win: number
  largest_loss: number
  max_drawdown: number
  profit_factor: number
  avg_hold_minutes: number
  exit_reasons: Record<string, number>
}

export interface StockBacktestResponse {
  summary: StockSummary
  days: StockDay[]
  trades: StockTrade[]
}

export interface StockOptimizeParams {
  ticker: string
  bar_interval: string
  num_iterations: number
  target_metric: string
  top_n: number
  quantity: number
}

export interface StockOptimizeResultEntry {
  rank: number
  ticker: string
  timeframe: string
  params: Record<string, number | string | boolean>
  total_pnl: number
  total_trades: number
  win_rate: number
  profit_factor: number
  max_drawdown: number
  avg_hold_minutes: number
  avg_win: number
  avg_loss: number
  largest_win: number
  largest_loss: number
  score: number
  exit_reasons: Record<string, number>
  days_traded: number
}

export interface StockOptimizeResponse {
  total_combinations_tested: number
  elapsed_seconds: number
  target_metric: string
  results: StockOptimizeResultEntry[]
}

export async function getAvailableTickers(): Promise<TickerInfo[]> {
  const { data } = await api.get('/stock-backtest/tickers')
  return data
}

export async function runStockBacktest(params: StockBacktestParams): Promise<StockBacktestResponse> {
  const { data } = await api.post('/stock-backtest/run', params, { timeout: 120000 })
  return data
}

export async function runStockOptimization(params: StockOptimizeParams): Promise<StockOptimizeResponse> {
  const { data } = await api.post('/stock-backtest/optimize', params, { timeout: 300000 })
  return data
}

export async function getSavedResults(): Promise<StockOptimizeResultEntry[]> {
  const { data } = await api.get('/stock-backtest/results')
  return data
}
