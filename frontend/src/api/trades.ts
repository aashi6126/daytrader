import api from './client'
import type { TradeListResponse, Trade, TradeEventListResponse, PriceSnapshotListResponse, QuotesResponse } from '../types'

export async function fetchTrades(params?: {
  trade_date?: string
  status?: string
  ticker?: string
  page?: number
  per_page?: number
}): Promise<TradeListResponse> {
  const { data } = await api.get('/trades', { params })
  return data
}

export async function fetchTickers(): Promise<string[]> {
  const { data } = await api.get('/trades/tickers')
  return data.tickers
}

export async function fetchTrade(id: number): Promise<Trade> {
  const { data } = await api.get(`/trades/${id}`)
  return data
}

export async function fetchTradeEvents(tradeId: number): Promise<TradeEventListResponse> {
  const { data } = await api.get(`/trades/${tradeId}/events`)
  return data
}

export async function fetchTradePrices(tradeId: number): Promise<PriceSnapshotListResponse> {
  const { data } = await api.get(`/trades/${tradeId}/prices`)
  return data
}

export async function fetchOpenQuotes(): Promise<QuotesResponse> {
  const { data } = await api.get('/trades/open/quotes')
  return data
}

export async function retakeTrade(tradeId: number): Promise<{ status: string; message: string; trade_id?: number }> {
  const { data } = await api.post(`/trades/${tradeId}/retake`)
  return data
}

export async function closeTrade(tradeId: number): Promise<{ status: string; message: string }> {
  const { data } = await api.post(`/trades/${tradeId}/close`)
  return data
}

export async function cancelTrade(tradeId: number): Promise<{ status: string; message: string }> {
  const { data } = await api.post(`/trades/${tradeId}/cancel`)
  return data
}
