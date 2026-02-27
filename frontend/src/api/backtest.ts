import api from './client'

export type SignalType = 'ema_cross' | 'vwap_cross' | 'ema_vwap' | 'orb' | 'vwap_rsi' | 'bb_squeeze' | 'rsi_reversal' | 'confluence'

export const ALL_SIGNAL_TYPES: SignalType[] = ['ema_cross', 'vwap_cross', 'ema_vwap', 'orb', 'vwap_rsi', 'bb_squeeze', 'rsi_reversal', 'confluence']

export interface BacktestParams {
  start_date: string
  end_date: string
  signal_type: SignalType | 'all'
  ema_fast: number
  ema_slow: number
  bar_interval: '5m' | '1m'
  rsi_period: number
  rsi_ob: number
  rsi_os: number
  orb_minutes: number
  atr_period: number
  atr_stop_mult: number
  afternoon_enabled: boolean
  entry_limit_below_percent: number
  quantity: number
  delta_target: number
  stop_loss_percent: number
  profit_target_percent: number
  trailing_stop_percent: number
  trailing_stop_after_scale_out_percent: number
  max_hold_minutes: number
  scale_out_enabled: boolean
  breakeven_trigger_percent: number
  min_confluence: number
  vol_threshold: number
  max_daily_trades: number
  max_daily_loss: number
  max_consecutive_losses: number
  entry_confirm_minutes: number
}

export interface BacktestTrade {
  trade_date: string
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
  scaled_out: boolean
  scaled_out_price: number | null
  underlying_price: number | null
  expiry_date: string | null
  dte: number
  delta: number | null
  entry_reason: string | null
  exit_detail: string | null
}

export interface BacktestDay {
  trade_date: string
  pnl: number
  total_trades: number
  winning_trades: number
  losing_trades: number
}

export interface BacktestSummary {
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

export interface BacktestResponse {
  summary: BacktestSummary
  days: BacktestDay[]
  trades: BacktestTrade[]
}

export async function runBacktest(params: BacktestParams): Promise<BacktestResponse> {
  const { data } = await api.post('/backtest/run', params, { timeout: 120000 })
  return data
}


// ── Optimizer types ──────────────────────────────────────────────

export interface OptimizeParams {
  start_date: string
  end_date: string
  bar_interval: '5m' | '1m'
  num_iterations: number
  target_metric: 'total_pnl' | 'profit_factor' | 'win_rate' | 'composite' | 'risk_adjusted'
  top_n: number
  afternoon_enabled: boolean
  scale_out_enabled: boolean
  quantity: number
}

export interface OptimizeResultEntry {
  rank: number
  params: Record<string, number | string>
  total_pnl: number
  total_trades: number
  win_rate: number
  profit_factor: number
  max_drawdown: number
  avg_hold_minutes: number
  score: number
  exit_reasons: Record<string, number>
  // Out-of-sample (walk-forward) metrics
  oos_total_pnl: number | null
  oos_total_trades: number | null
  oos_win_rate: number | null
  oos_profit_factor: number | null
  oos_score: number | null
}

export interface OptimizeResponse {
  total_combinations_tested: number
  elapsed_seconds: number
  target_metric: string
  results: OptimizeResultEntry[]
  // Walk-forward date ranges
  train_start: string | null
  train_end: string | null
  test_start: string | null
  test_end: string | null
}

export async function runOptimization(params: OptimizeParams): Promise<OptimizeResponse> {
  const { data } = await api.post('/backtest/optimize', params, { timeout: 300000 })
  return data
}
