export function formatCurrency(value: number): string {
  const sign = value >= 0 ? '' : '-'
  return `${sign}$${Math.abs(value).toFixed(2)}`
}

export function formatPercent(value: number): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`
}

export function formatTime(timestamp: string): string {
  // Backend stores UTC timestamps without a Z suffix — ensure JS treats them as UTC
  const d = new Date(timestamp.endsWith('Z') || timestamp.includes('+') ? timestamp : timestamp + 'Z')
  return d.toLocaleString('en-US', {
    timeZone: 'America/New_York',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  })
}

export function statusColor(status: string): string {
  switch (status) {
    case 'PENDING': return 'text-yellow-400'
    case 'FILLED': return 'text-blue-400'
    case 'STOP_LOSS_PLACED': return 'text-blue-400'
    case 'EXITING': return 'text-orange-400'
    case 'CLOSED': return 'text-gray-400'
    case 'CANCELLED': return 'text-red-400'
    default: return 'text-gray-400'
  }
}

export function pnlColor(value: number | null): string {
  if (value === null) return 'text-gray-400'
  return value >= 0 ? 'text-green-400' : 'text-red-400'
}
