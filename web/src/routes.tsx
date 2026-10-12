import { RequireKey } from './components/RequireKey'
import { Layout } from './components/Layout'
import { BacktestPage } from './pages/BacktestPage'
import { HomePage } from './pages/HomePage'
import { NotFoundPage } from './pages/NotFoundPage'
import { PortfolioPage } from './pages/PortfolioPage'
import { ScreenerPage } from './pages/ScreenerPage'
import { SettingsPage } from './pages/SettingsPage'
import { SignInPage } from './pages/SignInPage'
import { StockPage } from './pages/StockPage'

export const routes = [
  { path: '/signin', element: <SignInPage /> },
  {
    element: (
      <RequireKey>
        <Layout />
      </RequireKey>
    ),
    children: [
      { path: '/', element: <HomePage /> },
      { path: '/stocks/:symbol', element: <StockPage /> },
      { path: '/portfolio', element: <PortfolioPage /> },
      { path: '/backtest', element: <BacktestPage /> },
      { path: '/screener', element: <ScreenerPage /> },
      { path: '/settings', element: <SettingsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
