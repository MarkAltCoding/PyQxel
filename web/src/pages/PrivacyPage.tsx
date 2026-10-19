import type { ReactNode } from 'react'
import { Link } from 'react-router'

const UPDATED = 'October 19, 2026'
const CONTACT = 'https://github.com/MarkAltCoding'

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="stack" style={{ gap: 8 }}>
      <h2>{title}</h2>
      {children}
    </section>
  )
}

/** What PyQxel keeps about the people who use it, and why. Public, so it can be read
 * before signing in, as Google's sign-in requires. */
export function PrivacyPage() {
  return (
    <main>
      <article className="card privacy stack" style={{ gap: 20 }}>
        <header>
          <h1>Privacy policy</h1>
          <p className="subtitle">Last updated {UPDATED}</p>
        </header>
        <p>
          PyQxel is a personal research project run by Mark Altreuter. This page explains what it
          keeps about you when you use it, why, and who else sees it. It keeps only what it needs to
          run your account, and it never sells data, shows ads or uses analytics or tracking.
        </p>

        <Section title="When you sign in">
          <p>
            Signing in with GitHub or Google gives PyQxel two things: that provider’s permanent ID
            for your account, and your verified email address. They identify your PyQxel account, so
            the same person signing in with either provider reaches the same account. PyQxel does
            not keep your name, profile picture or anything else from those accounts, nor the access
            token the provider issues, which it uses once and discards.
          </p>
          <p>
            Signing in gives your browser a PyQxel API key, and PyQxel stores only a one-way hash of
            it. A cookie holds the sign-in in progress for up to ten minutes and is then deleted;
            PyQxel sets no other cookies.
          </p>
        </Section>

        <Section title="What your account holds">
          <ul>
            <li>
              Keys you choose to add for Anthropic, Financial Modeling Prep or Alpha Vantage. They
              are encrypted when stored and used only for your own requests, which those services
              bill to you.
            </li>
            <li>
              What you save: your portfolio holdings, backtests (with the code of custom
              strategies), simulations and AI research reports. Only your account can see them.
            </li>
            <li>Counts of your recent AI requests, to apply the limits you set.</li>
          </ul>
          <p>
            Custom strategy code runs in your own browser. When you backtest it, its code and the
            positions it produced are sent to PyQxel to score and save.
          </p>
        </Section>

        <Section title="Who else sees it">
          <ul>
            <li>
              PyQxel runs on Google Cloud, keeps its database at Neon and its cache at Upstash.
            </li>
            <li>
              The symbols you look up are sent to market data providers: Financial Modeling Prep and
              Alpha Vantage with your own keys, and Yahoo Finance and Nasdaq without anything about
              you. Company filings come from the SEC.
            </li>
            <li>
              When you ask for an AI research report, the company’s data is sent to Anthropic with
              your own key, under Anthropic’s terms.
            </li>
          </ul>
          <p>
            Google Cloud keeps request logs, including your IP address and browser, for 30 days, for
            running and securing the service.
          </p>
        </Section>

        <Section title="In your browser">
          <p>
            Your browser keeps your API key if you choose to stay signed in, your theme and the
            strategy code you are writing. That stays on your device; signing out removes the key.
          </p>
        </Section>

        <Section title="Deleting your data">
          <p>
            To delete your account and everything it holds, contact me through{' '}
            <a href={CONTACT}>GitHub</a>. Removing PyQxel’s access in your GitHub or Google settings
            stops it signing you in, but does not by itself delete your account.
          </p>
        </Section>

        <Section title="Children and changes">
          <p>
            PyQxel is not meant for children under 13. If this policy changes, this page will say
            so, with a new date above.
          </p>
        </Section>

        <p className="disclaimer">
          Questions? Reach me through <a href={CONTACT}>GitHub</a>.{' '}
          <Link to="/signin">Sign in</Link>
        </p>
      </article>
    </main>
  )
}
