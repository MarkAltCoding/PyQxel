import { NativeSignInLinks } from './auth/NativeSignInLinks'
import { RequireKey } from './components/RequireKey'
import { Layout } from './components/Layout'
import { BacktestPage } from './pages/BacktestPage'
import { HomePage } from './pages/HomePage'
import { MarketsPage } from './pages/MarketsPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { PortfolioPage } from './pages/PortfolioPage'
import { PrivacyPage } from './pages/PrivacyPage'
import { ScreenerPage } from './pages/ScreenerPage'
import { SetPasswordPage } from './pages/SetPasswordPage'
import { SettingsPage } from './pages/SettingsPage'
import { SignInPage } from './pages/SignInPage'
import { StockPage } from './pages/StockPage'

export const routes = [
  {
    // Every page sits under it, so a sign-in link that opens the app reaches it anywhere.
    element: <NativeSignInLinks />,
    children: [
      { path: '/signin', element: <SignInPage /> },
      // Where an emailed link leads, before there is a key.
      { path: '/signin/password', element: <SetPasswordPage /> },
      // Public, so it can be read before signing in.
      { path: '/privacy', element: <PrivacyPage /> },
      {
        element: (
          <RequireKey>
            <Layout />
          </RequireKey>
        ),
        children: [
          { path: '/', element: <HomePage /> },
          { path: '/stocks/:symbol', element: <StockPage /> },
          { path: '/markets', element: <MarketsPage /> },
          { path: '/portfolio', element: <PortfolioPage /> },
          { path: '/backtest', element: <BacktestPage /> },
          { path: '/screener', element: <ScreenerPage /> },
          { path: '/settings', element: <SettingsPage /> },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
]
