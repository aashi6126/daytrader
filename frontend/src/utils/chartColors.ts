export function getChartColors() {
  const style = getComputedStyle(document.documentElement)
  return {
    grid: style.getPropertyValue('--color-chart-grid').trim() || '#334155',
    axis: style.getPropertyValue('--color-chart-axis').trim() || '#64748b',
    ref: style.getPropertyValue('--color-chart-ref').trim() || '#475569',
    tooltipBg: style.getPropertyValue('--color-chart-tooltip-bg').trim() || '#1e293b',
    tooltipBorder: style.getPropertyValue('--color-chart-tooltip-border').trim() || '#334155',
    label: style.getPropertyValue('--color-chart-label').trim() || '#94a3b8',
    green: '#22c55e',
    red: '#ef4444',
    blue: '#60a5fa',
    lightRed: '#f87171',
    sky: '#38bdf8',
    amber: '#fbbf24',
  }
}
