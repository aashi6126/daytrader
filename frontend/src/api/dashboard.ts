import api from './client'
import type { DailyStats, PnLChartData, PnLSummaryData } from '../types'

export async function fetchDailyStats(tradeDate?: string): Promise<DailyStats> {
  const params = tradeDate ? { trade_date: tradeDate } : {}
  const { data } = await api.get('/dashboard/stats', { params })
  return data
}

export async function fetchPnLData(tradeDate?: string): Promise<PnLChartData> {
  const params = tradeDate ? { trade_date: tradeDate } : {}
  const { data } = await api.get('/dashboard/pnl', { params })
  return data
}

export async function fetchPnLSummary(period: 'weekly' | 'monthly'): Promise<PnLSummaryData> {
  const { data } = await api.get('/dashboard/pnl-summary', { params: { period } })
  return data
}

export interface SpyPrice {
  price: number | null
  change: number | null
  change_percent: number | null
  error: string | null
}

export async function fetchSpyPrice(): Promise<SpyPrice> {
  const { data } = await api.get('/dashboard/spy-price')
  return data
}

export interface WindowOverride {
  ignore_trading_windows: boolean
}

export async function fetchWindowOverride(): Promise<WindowOverride> {
  const { data } = await api.get('/dashboard/window-override')
  return data
}

export async function setWindowOverride(ignore: boolean): Promise<WindowOverride> {
  const { data } = await api.put('/dashboard/window-override', { ignore_trading_windows: ignore })
  return data
}

export interface MarketOrderOverride {
  use_market_orders: boolean
}

export async function fetchMarketOrderOverride(): Promise<MarketOrderOverride> {
  const { data } = await api.get('/dashboard/market-order-override')
  return data
}

export async function setMarketOrderOverride(use: boolean): Promise<MarketOrderOverride> {
  const { data } = await api.put('/dashboard/market-order-override', { use_market_orders: use })
  return data
}

export interface ActiveStrategy {
  strategy: string
  description: string
}

export async function fetchActiveStrategy(): Promise<ActiveStrategy> {
  const { data } = await api.get('/dashboard/strategy')
  return data
}

export interface TickerQuote {
  price: number | null
  change: number | null
  change_percent: number | null
}

export interface MarketOverview {
  vix: TickerQuote | null
  spy: TickerQuote | null
  qqq: TickerQuote | null
  error: string | null
}

export async function fetchMarketOverview(): Promise<MarketOverview> {
  const { data } = await api.get('/dashboard/vix')
  return data
}

export interface NgrokStatus {
  online: boolean
  url: string | null
  error: string | null
}

export async function fetchNgrokStatus(): Promise<NgrokStatus> {
  const { data } = await api.get('/dashboard/ngrok')
  return data
}

export interface TokenStatus {
  valid: boolean
  refresh_token_issued: string | null
  refresh_token_expires: string | null
  days_remaining: number | null
  error: string | null
}

export async function fetchTokenStatus(): Promise<TokenStatus> {
  const { data } = await api.get('/dashboard/token-status')
  return data
}

// --- Analytics ---

export interface HourBucket {
  hour: number
  label: string
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  total_pnl: number
  avg_pnl: number
}

export interface StrategyBucket {
  strategy: string
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  total_pnl: number
  avg_pnl: number
  profit_factor: number
}

export interface DayOfWeekBucket {
  day: number
  label: string
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  total_pnl: number
}

export interface HoldTimeBucket {
  label: string
  total_trades: number
  winning_trades: number
  win_rate: number
  avg_pnl: number
}

export interface StreakInfo {
  current_type: string
  current_count: number
  longest_win: number
  longest_loss: number
}

export interface AnalyticsData {
  period_label: string
  total_trades: number
  by_hour: HourBucket[]
  by_strategy: StrategyBucket[]
  by_day_of_week: DayOfWeekBucket[]
  by_hold_time: HoldTimeBucket[]
  streak: StreakInfo
}

export async function fetchAnalytics(days = 30): Promise<AnalyticsData> {
  const { data } = await api.get('/dashboard/analytics', { params: { days } })
  return data
}

export interface CandleData {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export async function fetchCandles(ticker = 'SPY', frequency = 5, tradeDate?: string): Promise<CandleData[]> {
  const params: Record<string, string | number> = { ticker, frequency }
  if (tradeDate) params.trade_date = tradeDate
  const { data } = await api.get('/dashboard/candles', { params })
  return data
}

export interface PivotLevels {
  pivot: number
  r1: number
  s1: number
  r2: number
  s2: number
}

export async function fetchPivotLevels(ticker: string, tradeDate?: string): Promise<PivotLevels | null> {
  const params: Record<string, string> = { ticker }
  if (tradeDate) params.trade_date = tradeDate
  const { data } = await api.get('/dashboard/pivots', { params })
  return data
}

export interface ChartMarker {
  time: number
  type: 'signal' | 'entry' | 'exit'
  direction: 'CALL' | 'PUT'
  label: string
  price: number | null
}

export async function fetchChartMarkers(ticker: string, tradeDate?: string): Promise<ChartMarker[]> {
  const params: Record<string, string> = { ticker }
  if (tradeDate) params.trade_date = tradeDate
  const { data } = await api.get('/dashboard/chart-markers', { params })
  return data
}
