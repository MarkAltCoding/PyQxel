import { Link } from 'react-router'
import { ContactLink, LegalFooter, LegalSection } from '../components/LegalSection'

const UPDATED = 'November 10, 2026'

/** The terms of using PyQxel and paying for its nightly refresh. Public, so they can be read
 * before signing in or paying, as Stripe requires. */
export function TermsPage() {
  return (
    <article className="card privacy stack" style={{ gap: 20 }}>
      <header>
        <h1>Terms of service</h1>
        <p className="subtitle">Last updated {UPDATED}</p>
      </header>
      <p>
        PyQxel is investment research software run by Mark Altreuter as an individual. By using it,
        on the website or in the app, you agree to these terms. If you do not agree, please do not
        use PyQxel.
      </p>

      <LegalSection title="What PyQxel is, and is not">
        <p>
          PyQxel shows prices, statistics, stock screens, strategy backtests, simulations, and
          AI-written research reports to help you study securities. It is a research tool. It does
          not give personalized investment advice, recommend that you buy, sell, or hold anything,
          manage money, hold your funds, or place trades, and it is not a financial adviser or
          broker. Backtests and simulations replay or model the past, which does not predict the
          future. Decisions you make with it are yours.
        </p>
      </LegalSection>

      <LegalSection title="Your account and your keys">
        <p>
          You are responsible for what is done with your account and its API keys, so keep them
          private. Market data and AI features run on API keys you add from other services, such as
          Finnhub, Tiingo, Financial Modeling Prep, EODHD, Alpha Vantage, and Anthropic. Those
          services bill you under their own plans and terms, which you agree to with them, including
          any limits on how their data may be used. PyQxel is not responsible for their charges,
          their data, or their availability.
        </p>
      </LegalSection>

      <LegalSection title="Using PyQxel fairly">
        <p>
          Do not use PyQxel to break the law or anyone’s terms, to try to reach other people’s
          accounts or data, to get around its limits or security, to overload it, or to resell its
          service or the data it shows. PyQxel may suspend or close an account that does.
        </p>
      </LegalSection>

      <LegalSection title="Data and AI can be wrong">
        <p>
          Prices, financial figures, and news come from outside services and can be late,
          incomplete, or wrong. AI-written reports can contain mistakes. Check what matters with
          other sources before relying on it.
        </p>
      </LegalSection>

      <LegalSection title="Paying for the nightly refresh">
        <p>
          Using PyQxel is free. The one paid feature is the optional nightly screener refresh, which
          runs on PyQxel’s cloud server. You pay what your refreshes cost that server, at Google
          Cloud’s price for the computing time they use, plus the fees Stripe charges on your own
          payments. Nothing is added for profit. A refresh is charged whether it succeeds or fails,
          since the server runs either way.
        </p>
        <ul>
          <li>
            <strong>Prepaid credit:</strong> buy any amount from $5 to $500. Stripe’s processing fee
            is added to the price, and a card issued outside the US has Stripe’s extra fee for it
            taken from the credit. Each refresh uses the credit up.
          </li>
          <li>
            <strong>Subscription:</strong> Stripe bills your card at the end of each week, month, or
            year, as you choose, for what your refreshes cost in that period after any credit, plus
            Stripe’s fees on that bill. You can cancel any time in Settings, through Stripe’s
            billing page; the subscription then ends at the end of the period, billing the refreshes
            up to then.
          </li>
          <li>
            With neither credit nor a subscription, your refresh pauses until you add one; nothing
            else changes.
          </li>
          <li>
            <strong>Taxes:</strong> where PyQxel is registered to collect sales tax or VAT, Stripe
            adds it to the price, based on the billing address you give on Stripe’s page, and the
            fees Stripe charges on the tax are passed on like its other fees.
          </li>
        </ul>
        <p>
          Payments are processed by Stripe under its own terms; PyQxel never sees your card. If
          Google Cloud’s or Stripe’s prices change, what you pay changes with them, and Settings
          shows the current figures. Refunds are covered by the{' '}
          <Link to="/refunds">refund policy</Link>. If you dispute a payment with your bank instead
          of asking for a refund, the $15 fee Stripe charges for the dispute is charged to your
          account.
        </p>
      </LegalSection>

      <LegalSection title="Ending your use">
        <p>
          You can delete your account at any time under Delete account in Settings, which also ends
          any subscription and refunds your unused credit. PyQxel may stop offering a feature, or
          the whole service, with notice on this site; if it does, unused credit is refunded.
        </p>
      </LegalSection>

      <LegalSection title="No warranty, and limits on liability">
        <p>
          PyQxel is provided as it is, without warranties of any kind, including that it will be
          accurate, available, or fit for any purpose. To the extent the law allows, PyQxel and its
          owner are not liable for any investment losses, or for indirect or consequential damages,
          arising from your use of it, and their total liability for any claim is limited to what
          you paid PyQxel in the 12 months before the claim.
        </p>
      </LegalSection>

      <LegalSection title="Age and changes">
        <p>
          You must be at least 13 to use PyQxel, and at least 18, or have a parent’s or guardian’s
          permission, to pay for it. If these terms change, this page will say so, with a new date
          above; continuing to use PyQxel after that means you accept them. Questions about these
          terms can go to <ContactLink />.
        </p>
      </LegalSection>

      <LegalFooter />
    </article>
  )
}
