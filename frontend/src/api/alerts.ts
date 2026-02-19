import api from './client'
import type { AlertListResponse } from '../types'

export async function fetchAlerts(params?: {
  alert_date?: string
  status?: string
  trading_window_only?: boolean
  page?: number
  per_page?: number
}): Promise<AlertListResponse> {
  const { data } = await api.get('/alerts', { params })
  return data
}
