import { Link } from 'react-router'
import { ContactLink, LegalFooter, LegalSection } from '../components/LegalSection'

const UPDATED = 'November 10, 2026'

/** When and how payments for the nightly refresh are refunded. Public, as Stripe requires. */
export function RefundPage() {
  return (
    <article className="card privacy stack" style={{ gap: 20 }}>
      <header>
        <h1>Refund policy</h1>
        <p className="subtitle">Last updated {UPDATED}</p>
      </header>
      <p>
        PyQxel charges only for its optional nightly screener refresh, at what it costs to run (see
        the <Link to="/terms">terms of service</Link>). This page explains what can be refunded.
      </p>

      <LegalSection title="Unused credit">
        <p>
          Prepaid credit you have not used is refundable at any time. Ask through <ContactLink />{' '}
          and it will be refunded to the card that paid for it. Deleting your account in Settings
          refunds it automatically. Stripe does not return its processing fee on a refunded payment,
          so the refund is the credit left, which never included that fee.
        </p>
      </LegalSection>

      <LegalSection title="Refreshes that already ran">
        <p>
          A refresh is charged for the server time it used, so refreshes that already ran, whether
          paid from credit or by a subscription bill, are not refunded. Cancel your subscription, or
          turn the nightly refresh off in Settings, to stop future charges.
        </p>
        <p>
          If a refresh failed because of a fault in PyQxel itself, rather than your keys or a data
          provider’s service, its cost is refunded: tell me which one, and it is returned to your
          credit.
        </p>
      </LegalSection>

      <LegalSection title="Billing mistakes">
        <p>
          If you were charged by mistake, such as twice for the same thing or for refreshes you had
          turned off, the whole charge is refunded, Stripe’s fee included.
        </p>
      </LegalSection>

      <LegalSection title="How to ask, and how long it takes">
        <p>
          Reach me through <ContactLink />, with the email address your account uses and the date of
          the payment. Refunds are made within a few days of the request, and banks usually show
          them 5 to 10 business days after that. Please ask before disputing a payment with your
          bank: a dispute costs a $15 fee, which is charged to your account.
        </p>
      </LegalSection>

      <LegalFooter />
    </article>
  )
}
