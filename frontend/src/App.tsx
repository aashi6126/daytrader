import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import api from './api/client'
import { fetchNgrokStatus, fetchTokenStatus, type NgrokStatus, type TokenStatus } from './api/dashboard'
import { useTheme } from './hooks/useTheme'
import AlertsHistory from './pages/AlertsHistory'
import Dashboard from './pages/Dashboard'
import TradeHistory from './pages/TradeHistory'
import Backtest from './pages/Backtest'

function App() {
  const [paperTrade, setPaperTrade] = useState<boolean | null>(null)
  const [toggling, setToggling] = useState(false)
  const [ngrok, setNgrok] = useState<NgrokStatus | null>(null)
  const [token, setToken] = useState<TokenStatus | null>(null)
  const { theme, toggleTheme } = useTheme()

  useEffect(() => {
    api.get('/dashboard/mode').then(({ data }) => setPaperTrade(data.paper_trade))
    fetchNgrokStatus().then(setNgrok).catch(() => setNgrok({ online: false, url: null, error: 'fetch failed' }))
    fetchTokenStatus().then(setToken).catch(() => null)
    const interval = setInterval(() => {
      fetchNgrokStatus().then(setNgrok).catch(() => setNgrok({ online: false, url: null, error: 'fetch failed' }))
      fetchTokenStatus().then(setToken).catch(() => null)
    }, 30_000)
    return () => clearInterval(interval)
  }, [])

  const toggleMode = async () => {
    if (paperTrade === null) return
    const newMode = !paperTrade
    setToggling(true)
    try {
      await api.put('/dashboard/mode', { paper_trade: newMode })
      setPaperTrade(newMode)
    } finally {
      setToggling(false)
    }
  }

  return (
    <BrowserRouter>
      <nav className="bg-surface border-b border-subtle px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center gap-6">
          <span className="text-lg font-bold text-heading">DayTrader</span>
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `text-sm ${isActive ? 'text-heading font-medium' : 'text-secondary hover:text-primary'}`
            }
          >
            Dashboard
          </NavLink>
          <NavLink
            to="/history"
            className={({ isActive }) =>
              `text-sm ${isActive ? 'text-heading font-medium' : 'text-secondary hover:text-primary'}`
            }
          >
            Trade History
          </NavLink>
          <NavLink
            to="/alerts"
            className={({ isActive }) =>
              `text-sm ${isActive ? 'text-heading font-medium' : 'text-secondary hover:text-primary'}`
            }
          >
            Alerts
          </NavLink>
          <NavLink
            to="/backtest"
            className={({ isActive }) =>
              `text-sm ${isActive ? 'text-heading font-medium' : 'text-secondary hover:text-primary'}`
            }
          >
            Backtest
          </NavLink>

          <div className="ml-auto flex items-center gap-4">
            <button
              onClick={toggleTheme}
              className="p-1.5 rounded-lg bg-elevated hover:bg-hover text-secondary hover:text-primary"
              title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            >
              {theme === 'dark' ? (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
                </svg>
              ) : (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
                </svg>
              )}
            </button>
            {token && (
              <div
                className="flex items-center gap-1.5"
                title={
                  token.valid
                    ? `Token expires: ${token.refresh_token_expires ? new Date(token.refresh_token_expires).toLocaleDateString() : '?'} (${token.days_remaining}d)`
                    : `Token: ${token.error || 'expired'}`
                }
              >
                <span className={`w-2 h-2 rounded-full ${
                  !token.valid ? 'bg-red-400' : (token.days_remaining ?? 0) <= 2 ? 'bg-yellow-400 animate-pulse' : 'bg-green-400'
                }`} />
                <span className={`text-xs ${
                  !token.valid ? 'text-red-400' : (token.days_remaining ?? 0) <= 2 ? 'text-yellow-400' : 'text-secondary'
                }`}>
                  {!token.valid ? 'Token expired' : `${token.days_remaining}d`}
                </span>
              </div>
            )}
            {ngrok && (
              <div className="flex items-center gap-1.5" title={ngrok.online ? `ngrok: ${ngrok.url}` : `ngrok: ${ngrok.error || 'offline'}`}>
                <span className={`w-2 h-2 rounded-full ${ngrok.online ? 'bg-green-400 animate-pulse' : 'bg-red-400'}`} />
                <span className={`text-xs ${ngrok.online ? 'text-green-400' : 'text-red-400'}`}>
                  ngrok
                </span>
              </div>
            )}
            {paperTrade !== null && (
              <div className="flex items-center gap-2">
                <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                  paperTrade
                    ? 'bg-yellow-400/10 text-yellow-400'
                    : 'bg-green-400/10 text-green-400'
                }`}>
                  {paperTrade ? 'Paper' : 'Live'}
                </span>
                <button
                  onClick={toggleMode}
                  disabled={toggling}
                  className={`relative w-9 h-5 rounded-full transition-colors ${
                    paperTrade ? 'bg-yellow-600' : 'bg-green-600'
                  } ${toggling ? 'opacity-50' : 'cursor-pointer'}`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                      !paperTrade ? 'translate-x-4' : ''
                    }`}
                  />
                </button>
              </div>
            )}
          </div>
        </div>
      </nav>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/history" element={<TradeHistory />} />
        <Route path="/alerts" element={<AlertsHistory />} />

        <Route path="/backtest" element={<Backtest />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
