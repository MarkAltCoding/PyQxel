import { createBrowserRouter, RouterProvider } from 'react-router'
import { ExternalLinkGuard } from './components/ExternalLinkGuard'
import { UpdateBanner } from './components/UpdateBanner'
import { routes } from './routes'

const router = createBrowserRouter(routes)

export function App() {
  return (
    <>
      <RouterProvider router={router} />
      <ExternalLinkGuard />
      <UpdateBanner />
    </>
  )
}
