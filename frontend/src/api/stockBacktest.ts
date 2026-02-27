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
  entry_confirm_minutes: number
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
  entry_reason: string | null
  exit_detail: string | null
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
  avg_entry_price: number
  max_entry_price: number
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
  avg_entry_price: number
  max_entry_price: number
  // Out-of-sample (walk-forward) metrics
  oos_total_pnl?: number | null
  oos_total_trades?: number | null
  oos_win_rate?: number | null
  oos_profit_factor?: number | null
  oos_max_drawdown?: number | null
  oos_score?: number | null
  // Monte Carlo bootstrap confidence
  mc_win_pct?: number | null
  mc_median_pnl?: number | null
  mc_p5_pnl?: number | null
  mc_p95_pnl?: number | null
  // Market cap tier
  market_cap_tier?: string | null
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

export async function clearSavedResults(): Promise<void> {
  await api.delete('/stock-backtest/results')
}

export async function getSavedResults(minTrades: number = 0, limit: number = 0): Promise<StockOptimizeResultEntry[]> {
  const params: Record<string, number> = {}
  if (minTrades > 0) params.min_trades = minTrades
  if (limit > 0) params.limit = limit
  const { data } = await api.get('/stock-backtest/results', { params })
  return data
}

// ── Batch Optimize ──────────────────────────────────────────────

export interface BatchOptimizeRequest {
  iterations: number
  metric: string
  min_trades: number
  market_cap_tier: string
  tickers?: string[]
}

export interface MarketCapTier {
  value: string
  label: string
  count: number
}

export interface BatchOptimizeStatus {
  status: 'idle' | 'running' | 'completed' | 'failed'
  progress: string
  elapsed_seconds: number
  results_count: number
  error: string
}

export async function getMarketCapTiers(): Promise<MarketCapTier[]> {
  const { data } = await api.get('/stock-backtest/tiers')
  return data
}

export async function startBatchOptimize(req: BatchOptimizeRequest): Promise<BatchOptimizeStatus> {
  const { data } = await api.post('/stock-backtest/batch-optimize', req)
  return data
}

export async function getBatchOptimizeStatus(): Promise<BatchOptimizeStatus> {
  const { data } = await api.get('/stock-backtest/batch-optimize/status')
  return data
}

// ── Search & Download ────────────────────────────────────────────

export interface SearchResult {
  symbol: string
  has_data: boolean
}

export interface DownloadResponse {
  ok: boolean
  symbol: string
  message: string
  files: number
  total_rows: number
}

export async function searchSymbols(query: string): Promise<SearchResult[]> {
  const { data } = await api.post(`/stock-backtest/search?query=${encodeURIComponent(query)}`)
  return data
}

export async function downloadSymbolData(symbol: string): Promise<DownloadResponse> {
  const { data } = await api.post(`/stock-backtest/download/${encodeURIComponent(symbol)}`, {}, { timeout: 600000 })
  return data
}

// ── Favorites ────────────────────────────────────────────────────

export interface FavoriteStrategy {
  id: number
  ticker: string
  strategy_name: string
  direction: string | null
  params: Record<string, number | string | boolean>
  summary: Record<string, number> | null
  notes: string | null
  created_at: string
}

export interface SaveFavoriteRequest {
  ticker: string
  strategy_name: string
  direction?: string
  params: Record<string, number | string | boolean>
  summary?: Record<string, number>
  notes?: string
}

export async function getFavorites(): Promise<FavoriteStrategy[]> {
  const { data } = await api.get('/stock-backtest/favorites')
  return data
}

export async function saveFavorite(req: SaveFavoriteRequest): Promise<FavoriteStrategy> {
  const { data } = await api.post('/stock-backtest/favorites', req)
  return data
}

export async function deleteFavorite(id: number): Promise<void> {
  await api.delete(`/stock-backtest/favorites/${id}`)
}

// ── Strategy enable/disable (multi-strategy) ────────────────────

export interface EnableStrategyRequest {
  ticker: string
  timeframe: string
  signal_type: string
  params: Record<string, number | string | boolean>
}

export interface DisableStrategyRequest {
  ticker: string
  timeframe: string
  signal_type: string
}

export interface EnabledStrategyEntry {
  ticker: string
  timeframe: string
  signal_type: string
  params?: Record<string, number | string | boolean>
  enabled_at?: string
}

export interface EnabledStrategiesResponse {
  strategies: EnabledStrategyEntry[]
}

export async function getStrategyStatus(): Promise<EnabledStrategiesResponse> {
  const { data } = await api.get('/strategies/enabled')
  return data
}

export async function enableStrategy(req: EnableStrategyRequest): Promise<EnabledStrategiesResponse> {
  const { data } = await api.post('/strategies/enable', req)
  return data
}

export async function disableStrategy(req: DisableStrategyRequest): Promise<EnabledStrategiesResponse> {
  const { data } = await api.post('/strategies/disable', req)
  return data
}
