import { createBrowserRouter, RouterProvider } from 'react-router'
import { ExternalLinkGuard } from './components/ExternalLinkGuard'
import { routes } from './routes'

const router = createBrowserRouter(routes)

export function App() {
  return (
    <>
      <RouterProvider router={router} />
      <ExternalLinkGuard />
    </>
  )
}
