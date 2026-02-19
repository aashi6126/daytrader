import api from './client'
import type { DailyStats, PnLChartData, PnLSummaryData } from '../types'

export async function fetchDailyStats(): Promise<DailyStats> {
  const { data } = await api.get('/dashboard/stats')
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

export interface ActiveStrategy {
  strategy: string
  description: string
}

export async function fetchActiveStrategy(): Promise<ActiveStrategy> {
  const { data } = await api.get('/dashboard/strategy')
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
