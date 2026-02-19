import { useEffect, useRef, useCallback, useState } from 'react'
import type { WSMessage } from '../types'

export function useWebSocket(url: string) {
  const wsRef = useRef<WebSocket | null>(null)
  const [lastMessage, setLastMessage] = useState<WSMessage | null>(null)
  const [isConnected, setIsConnected] = useState(false)
  const reconnectRef = useRef<ReturnType<typeof setTimeout>>()

  const connect = useCallback(() => {
    const ws = new WebSocket(url)

    ws.onopen = () => setIsConnected(true)

    ws.onmessage = (event) => {
      try {
        setLastMessage(JSON.parse(event.data))
      } catch { /* ignore */ }
    }

    ws.onclose = () => {
      setIsConnected(false)
      reconnectRef.current = setTimeout(connect, 3000)
    }

    ws.onerror = () => ws.close()

    wsRef.current = ws
  }, [url])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { lastMessage, isConnected }
}
