import { Link } from 'react-router'

export function NotFoundPage() {
  return (
    <div className="empty">
      <h1>Page not found</h1>
      <p style={{ marginTop: 8 }}>
        <Link to="/">Back to search</Link>
      </p>
    </div>
  )
}
