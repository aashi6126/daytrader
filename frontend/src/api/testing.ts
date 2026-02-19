import api from './client'

export async function sendTestAlert(payload: {
  ticker: string
  action: 'BUY_CALL' | 'BUY_PUT'
  secret: string
  price?: number
}): Promise<{ status: string; message: string; trade_id?: number }> {
  const { data } = await api.post('/webhook', { ...payload, source: 'test' })
  return data
}

export async function testCloseTrade(
  tradeId: number,
  pnlPercent: number,
): Promise<{ status: string; message: string }> {
  const { data } = await api.post('/testing/close-trade', {
    trade_id: tradeId,
    pnl_percent: pnlPercent,
  })
  return data
}
