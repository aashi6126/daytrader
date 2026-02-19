import { useMemo } from 'react'
import { useTheme } from './useTheme'
import { getChartColors } from '../utils/chartColors'

export function useChartColors() {
  const { theme } = useTheme()
  return useMemo(() => getChartColors(), [theme])
}
