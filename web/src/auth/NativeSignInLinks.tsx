import { useEffect } from 'react'
import { Outlet, useNavigate } from 'react-router'
import { isNativeApp, listenForSignInLinks } from './nativeSignIn'

/**
 * In the native app, takes each `pyqxel://signin#…` link the system browser opens the
 * app with to the sign-in page, which finishes the sign-in. In a browser it does nothing.
 */
export function NativeSignInLinks() {
  const navigate = useNavigate()
  useEffect(() => {
    if (!isNativeApp()) return
    return listenForSignInLinks((fragment) => navigate(`/signin#${fragment}`))
  }, [navigate])
  return <Outlet />
}
