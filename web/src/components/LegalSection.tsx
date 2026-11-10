import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { useAuth } from '../auth/useAuth'

/** Where to reach PyQxel's owner about the account, payments, or these pages. */
const CONTACT = 'https://github.com/MarkAltCoding'

/** One titled section of a policy page: the privacy policy, terms, or refund policy. */
export function LegalSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="stack" style={{ gap: 8 }}>
      <h2>{title}</h2>
      {children}
    </section>
  )
}

/** A link to the owner's contact page, which opens outside PyQxel. */
export function ContactLink({ children = 'GitHub' }: { children?: ReactNode }) {
  return (
    <a href={CONTACT} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  )
}

/** The last line of a policy page: how to ask a question, and the way back. */
export function LegalFooter() {
  const { apiKey } = useAuth()
  return (
    <p className="disclaimer">
      Questions? Reach me through <ContactLink />.{' '}
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
