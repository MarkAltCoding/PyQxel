import { Navigate, useLocation } from 'react-router'

/** Sends an old address to where its page lives now, keeping its query and its `#card`, so
 * bookmarks and links written before a page moved still land on the same card. */
export function MovedTo({ to }: { to: string }) {
  const { search, hash } = useLocation()
  return <Navigate to={{ pathname: to, search, hash }} replace />
}
