import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { useAuth } from '../auth/useAuth'

const UPDATED = 'November 6, 2026'
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
  const { apiKey } = useAuth()
  return (
    <>
      <article className="card privacy stack" style={{ gap: 20 }}>
        <header>
          <h1>Privacy policy</h1>
          <p className="subtitle">Last updated {UPDATED}</p>
        </header>
        <p>
          PyQxel is a personal research project run by Mark Altreuter. This page explains what it
          keeps about you when you use it, why, and who else sees it. It keeps only what it needs to
          run your account, and it never sells data, shows ads, or uses analytics or tracking.
        </p>

        <Section title="When you sign in">
          <p>
            Signing in with GitHub, Google, or Apple (where offered) gives PyQxel two things: that
            provider’s permanent ID for your account, and your verified email address. They identify
            your PyQxel account, so the same person signing in with either provider reaches the same
            account. PyQxel asks for nothing else, not even your name or profile picture, and does
            not keep the access token the provider issues, which it uses once and discards. PyQxel
            does not log who signs in.
          </p>
          <p>
            Signing in with an email and password (where offered) keeps your email address and a
            one-way Argon2id hash of your password, never the password itself. Your email address is
            proven before it is used: PyQxel emails you a link, and only when you open it and choose
            a password is your account made, or the password added to the account already using that
            address. PyQxel keeps a hash of the link’s code until it is used, replaced, or an hour
            has passed. The emails are sent through Resend, which sees your address and the email;
            PyQxel sends no other email and no newsletters.
          </p>
          <p>
            Signing in gives your browser a PyQxel API key that works for 30 days, and PyQxel stores
            only a one-way hash of it. A cookie holds the sign-in in progress for up to ten minutes
            and is then deleted; PyQxel sets no other cookies. The PyQxel app signs in through your
            device’s browser, which hands the app a one-time code; PyQxel keeps a hash of that code
            for up to five minutes, until the app trades it for its key.
          </p>
          <p>
            To slow down abuse, sign-in attempts and emailed links are counted by IP address and by
            email address for up to a day, then forgotten. Google Cloud, which hosts PyQxel, records
            the IP address and page of each request in its access logs.
          </p>
        </Section>

        <Section title="What your account holds">
          <ul>
            <li>
              What you save: your portfolio holdings, backtests (with the code of custom
              strategies), simulations, AI research reports, and AI reviews of your portfolio,
              including answers you paste from Claude outside the app. Only your account can see
              them.
            </li>
            <li>
              Counts of your recent AI requests, to apply the limits you set. Your conversations
              with the strategy coding assistant are not stored: only that an answer was written.
            </li>
            <li>
              Daily prices fetched on your own keys, kept so later pages and backtests reuse them
              instead of spending your plans’ requests again. Only your account can see them. They
              are deleted when unused for 30 days, when you delete them in Settings, or with your
              account.
            </li>
            <li>
              How many requests PyQxel sent on each of your keys, by hour for about 40 days, and
              which symbols it asked Tiingo for each month, so Settings can show how much of each
              plan is used. They are filed under a one-way hash of the key, never the key itself.
            </li>
          </ul>
          <p>
            Keys you add for Anthropic, Tiingo, Financial Modeling Prep, EODHD, Alpha Vantage, or
            Finnhub stay in your browser, and PyQxel does not store them unless you turn on the
            nightly screener refresh. Each of your requests carries them over an encrypted
            connection, and the server uses them for that request only, keeps them out of its logs
            and error messages, and forgets them when it answers. Those services bill your usage to
            you. Signing out removes them from your browser. The PyQxel app keeps them, and its
            sign-in, in the iPhone’s Keychain, on that device only, or on a Mac in a file only your
            account can read; signing out or deleting the app removes them.
          </p>
          <p>
            The nightly screener refresh, which is off unless you turn it on in Settings, builds
            your own stock screener while your browser is closed, so it needs your keys when you are
            not there. Turning it on stores your Finnhub key and the Tiingo, Financial Modeling
            Prep, EODHD, and Alpha Vantage keys you choose to let it use on PyQxel’s server,
            encrypted, along with the time you chose, your time zone, and how many stocks it may
            fetch prices for. Your Anthropic key is never stored. The keys are decrypted only to run
            your refresh, used only for your own screener, and kept out of logs. Each time you
            change a key in Settings, the stored copy is replaced. Turning the refresh off, or
            deleting your account, deletes the stored keys at once. Your screener’s results, and a
            record of each refresh (when it ran, how many stocks it found, and any error), are kept
            with your account, seen only by you, until you delete it.
          </p>
          <p>
            Custom strategy code runs in your own browser. When you backtest it, its code and the
            positions it produced are sent to PyQxel to score and save.
          </p>
        </Section>

        <Section title="Who else sees it">
          <ul>
            <li>
              PyQxel runs on Google Cloud, keeps its database at Neon and its cache at Upstash, and
              sends sign-in emails through Resend.
            </li>
            <li>
              The symbols you look up are sent to market data providers with your own keys: Tiingo,
              Financial Modeling Prep, EODHD, Alpha Vantage, and Finnhub. If you turn on the nightly
              screener refresh, it requests every listed US stock from Finnhub, and as many as you
              choose from the price-history providers you choose, on your keys. Company filings come
              from the SEC, and exchange rates from the European Central Bank.
            </li>
            <li>
              When you use an AI feature, what it needs is sent to Anthropic with your own key,
              under Anthropic’s terms: a company’s data for a research report, your holdings, their
              statistics, and the goals you enter for a portfolio review, and your strategy code,
              its last run’s output, and your questions for the coding assistant.
            </li>
          </ul>
          <p>
            Google Cloud keeps request logs, including your IP address and browser, for 30 days, for
            running and securing the service.
          </p>
        </Section>

        <Section title="In your browser">
          <p>
            Your browser keeps your API key if you choose to stay signed in, your theme, the
            strategy code you are writing, which plan each of your keys is on, and which pages you
            chose to load without asking first. That stays on your device; signing out removes the
            key.
          </p>
        </Section>

        <Section title="Deleting your data">
          <p>
            To delete your account and everything it holds, including any keys the nightly screener
            refresh stored, use Delete account in Settings. It takes effect at once and cannot be
            undone. To delete only the stored keys, turn the nightly refresh off. If you can no
            longer sign in, contact me through{' '}
            <a href={CONTACT} target="_blank" rel="noopener noreferrer">
              GitHub
            </a>{' '}
            and I will delete it for you. Removing PyQxel’s access in your GitHub or Google settings
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
          Questions? Reach me through{' '}
          <a href={CONTACT} target="_blank" rel="noopener noreferrer">
            GitHub
          </a>
          . {apiKey ? <Link to="/">Back to PyQxel</Link> : <Link to="/signin">Sign in</Link>}
        </p>
      </article>
    </>
  )
}
