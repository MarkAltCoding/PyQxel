import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { useAuth } from '../auth/useAuth'

/** Where to reach PyQxel's owner about the account, payments, or these pages. */
const SUPPORT_EMAIL = 'pyqxel.app@gmail.com'

/** One titled section of a policy page: the privacy policy, terms, or refund policy. */
export function LegalSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="stack" style={{ gap: 8 }}>
      <h2>{title}</h2>
      {children}
    </section>
  )
}

/** PyQxel's support email address, as a link that starts an email to it. */
export function ContactLink({ children = SUPPORT_EMAIL }: { children?: ReactNode }) {
  return (
    <a href={`mailto:${SUPPORT_EMAIL}`} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  )
}

/** The last line of a policy page: how to ask a question, and the way back. */
export function LegalFooter() {
  const { apiKey } = useAuth()
  return (
    <p className="disclaimer">
      Questions? Email <ContactLink />.{' '}
      {apiKey ? <Link to="/">Back to PyQxel</Link> : <Link to="/signin">Sign in</Link>}
    </p>
  )
}

/** Links to every policy page, for page footers. */
export function PolicyLinks() {
  return (
    <>
      <Link to="/privacy">Privacy</Link> · <Link to="/terms">Terms</Link> ·{' '}
      <Link to="/refunds">Refunds</Link>
    </>
  )
}
