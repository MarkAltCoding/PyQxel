# App Store readiness

PyQxel's native app (Tauri 2, in `web/src-tauri/`) is ready to submit to the App Store
(iPhone and iPad) and the Mac App Store, but has not been submitted. This page holds what the
submission needs: what is already in the app, the listing text, the App Privacy answers, the
notes for App Review, the demo account plan, and the steps left. Anything here that differs
from App Store Connect when the app is submitted should be updated here too.

## Already in the app

| Requirement | Where |
| --- | --- |
| "Not investment advice" notice | `web/src/components/AdviceNotice.tsx`: shown once before any page, and must be acknowledged. The footer of every page and the sign-in page repeat it, and AI reports carry their own disclaimer. |
| Privacy manifest | `web/src-tauri/PrivacyInfo.xcprivacy`, bundled into the iOS app (`gen/apple/project.yml`) and the Mac app (`tauri.conf.json`, `bundle.macOS.files`). No tracking. It declares the data kept with an account and the one API category with a required reason the app calls: file timestamps (`C617.1`), from Rust's standard library. |
| Mac App Sandbox | `web/src-tauri/Entitlements.plist`: the sandbox, outgoing network connections, and the Downloads folder (for the prompt file Claude users download). Applied when the app is signed. |
| Export compliance | `ITSAppUsesNonExemptEncryption = false` in the iOS Info.plist (`gen/apple/project.yml`) and the Mac one (`web/src-tauri/Info.plist`, merged by `tauri build`). The app's only encryption is HTTPS through the operating system, which is exempt, so App Store Connect asks no export questions. |
| Category | Finance: `bundle.category` in `tauri.conf.json` and `LSApplicationCategoryType` on iOS. |
| Account deletion (guideline 5.1.1(v)) | Settings, Delete account. |
| Sign-in | An email and password inside the app (the account is made, or the password reset, by a link emailed to the address, which opens the website); GitHub and Google through the system browser, returning by a `pyqxel://signin` link; an API key as a fallback. Sign in with Apple is built but off (see below). |

## Before submitting

1. **Join the Apple Developer Program** ($99 a year). The free Personal Team used for the
   iPhone install cannot submit.
2. **Turn on Sign in with Apple.** Guideline 4.8 requires an option such as Sign in with
   Apple in an app that offers Google sign-in; PyQxel's own email and password do not
   count, since the guideline asks for a login service that can hide the email address. The README's *Signing in with GitHub and
   Google* section lists the Services ID, key and the five settings it takes.
3. **Register the app**: the App ID `com.markaltcoding.pyqxel` in the developer portal, then
   one App Store Connect record for iOS and macOS (one record covers both platforms).
4. **Version numbers**: `version` in `tauri.conf.json` (`0.1.0` now) becomes both the
   marketing version and, on iOS, the build number; each upload needs a higher build number.
5. **Screenshots** (taken with the demo account, after acknowledging the notice):
   - iPhone 6.9" (1320 × 2868): Stock page with chart, Backtest results, Portfolio risk,
     Screener, an AI report.
   - iPad 13" (2064 × 2752): the app targets iPad too (`TARGETED_DEVICE_FAMILY = 1,2`). To
     skip iPad, set it to `1` before submitting.
   - Mac (2880 × 1800).
6. **Build and upload**:
   - iOS: `npx tauri ios build --export-method app-store-connect` (in `web/`), with the
     team's distribution signing, then upload the `.ipa` with Transporter or
     `xcrun altool`.
   - Mac: `APPLE_SIGNING_IDENTITY="Apple Distribution: …" npx tauri build --bundles app`,
     with the Mac App Store provisioning profile added to `bundle.macOS.files` as
     `embedded.provisionprofile`. Then wrap the app with
     `productbuild --sign "3rd Party Mac Developer Installer: …" --component PyQxel.app /Applications PyQxel.pkg`,
     and upload the `.pkg` with Transporter.
7. **Check the sandboxed Mac build** works end to end: signing in, prices, a Python and a
   C++ backtest, downloading a Claude prompt.
8. **Set up the demo account** (below) and paste the review notes into App Store Connect.

## Listing text

**Name:** PyQxel

**Subtitle** (30 characters at most): Stock research and backtests

**Promotional text** (170 at most, can change without review):
Research stocks, test trading ideas on years of history, and see the risk in your
portfolio, using market data and AI on your own provider keys.

**Description:**

> PyQxel is an investment research workbench for investors who want to understand what they
> own and test ideas before they act on them.
>
> RESEARCH A STOCK
> Price history, valuation and quality metrics, factor exposures, volatility forecasts,
> trading signals and recent news, each explained in plain language with an "i" beside it.
>
> BACKTEST A STRATEGY
> Replay built-in rules, such as moving-average crossovers, or your own strategy written in
> Python or C++, on years of daily prices. Your code runs on your device; PyQxel scores the
> positions it takes and shows returns, drawdowns and risk against simply holding.
>
> UNDERSTAND YOUR PORTFOLIO
> Enter your holdings to see how they move together, what could happen in a bad year from
> thousands of simulations, and how to rebalance toward the weights you want.
>
> SCREEN THE MARKET
> Filter US stocks by size, sector, valuation, growth and momentum.
>
> AI THAT EXPLAINS
> With your own Anthropic key, Claude writes research reports on a company and reviews of
> your portfolio, and helps you write strategy code.
>
> YOUR KEYS, YOUR DATA
> PyQxel uses market data from Tiingo, Financial Modeling Prep, EODHD, Alpha Vantage and
> Finnhub on keys you add, starting with their free plans. Your keys stay on your device
> unless you turn on the nightly screener refresh. No ads, no tracking.
>
> PyQxel is a research tool, not investment advice. It does not trade or hold money, and
> past results do not predict future ones.

**Keywords** (100 characters at most, commas without spaces):
`stocks,backtest,portfolio,investing,screener,research,etf,risk,quant,python,strategy,factor`

**Primary category:** Finance. **Secondary:** Education.

**Age rating:** answer "None" to every content question. The coding assistant and AI reports
generate text with Claude on the user's own key, which the questionnaire's AI and
user-generated content questions may ask about; they produce research and code only.

**URLs:**
- Privacy policy: `https://<service-url>/privacy`
- Support: `https://github.com/MarkAltCoding/PyQxel/issues`
- Marketing (optional): `https://<service-url>/`

**Copyright:** © 2026 Mark Altreuter

## App Privacy answers

These must match `PrivacyInfo.xcprivacy` and the privacy policy page. Nothing is used for
tracking, and every type below is linked to the user and used only for App Functionality.

| App Store Connect type | What it is in PyQxel |
| --- | --- |
| Contact Info: Email Address | The verified email that identifies the account. |
| Identifiers: User ID | The account ID, and the GitHub, Google or Apple ID it signs in with. |
| Financial Info: Other Financial Info | Portfolio holdings. |
| User Content: Other User Content | Saved backtests with strategy code, simulations, AI reports and portfolio reviews. |
| Other Data | Provider API keys, stored encrypted only for users who turn on the nightly screener refresh. |

Not collected: name, location, contacts, browsing history, usage data, diagnostics,
purchases. Requests reach Google Cloud's access logs, which keep IP addresses for 30 days
for security. Apple counts that as not collected because it is not linked to the user
or kept beyond security purposes, but recheck the definition when submitting.

Two more stores need no label, but recheck them too when submitting. Daily market prices
kept for each account (`stored_price_history`) are third-party market data, not data
about the user. Counts of the requests PyQxel sent on each provider key
(`provider_usage`, `provider_usage_symbols`) are filed under a one-way hash of the key,
with no account ID, and are deleted after about 40 days.

## Notes for App Review

Paste into *App Review Information → Notes*, with the demo account's values filled in:

> PyQxel is an investment research tool. It does not trade, hold money, or give
> personalized advice; a notice says so on first launch and must be acknowledged.
>
> SIGNING IN: Under "Sign in to PyQxel", enter the demo account's email, app-review@…,
> and password, …, then press Sign in. The GitHub, Google and Apple buttons open the
> device's browser and return to the app.
>
> MARKET DATA: PyQxel uses market data from providers on the user's own API keys (users
> sign up for free plans with those providers). For review, open Settings and paste the keys
> below into their fields: Finnhub …, Tiingo …, Alpha Vantage …, Anthropic … (limited to
> a small monthly spend). Then try: search "AAPL" for the stock page; Backtest, enter
> SPY and press "Backtest SPY"; Portfolio, which holds a sample portfolio; Screener.
>
> CODE: Strategies users write in Python or C++ run inside the app's WebKit web view as
> WebAssembly (Pyodide and Clang), in the web view's sandbox. They compute trading
> positions from price data and cannot change the app's features. No code is downloaded
> to change the app.
>
> AI: Reports and the coding assistant call Anthropic's API with the user's own key.
>
> PURCHASES: The app sells nothing. Data providers bill their own plans, outside the app.
>
> ACCOUNT DELETION: Settings, Delete account.

### Review risks to check before submitting

- **Guideline 4.2 (minimum functionality).** The app shares its pages with the website, so
  App Review could call it a repackaged website. Its pages are bundled, not loaded from the
  web, and it runs strategy code on the device, signs in through the system browser and
  opens by its own links. Native touches, such as share sheets or notifications when the
  nightly screener finishes, would strengthen the case.
- **Guideline 3.1.1 (payments).** Settings names providers' paid plans and what they add. It
  links to no purchase and sells nothing, which is allowed, but if App Review objects, hide
  plan prices in the native app.
- **Guideline 4.8 (sign-in).** Sign in with Apple must be on before submitting, as above.
- **Free-plan quotas.** A reviewer on free keys can use up a provider's daily quota, after
  which pages show less data. Use fresh keys for the review and do not share them with
  other testing.

## Demo account plan

App Review needs an account it can sign in to without creating its own, and data to
look at.

1. **Create the account** on the production server, with an address that receives mail:
   `venv/bin/python -m app.jobs.users create app-review@<your-domain>` against the
   production database. Then give it a password: on the website's sign-in page choose
   **Forgot password?**, and open the emailed link. The email and password go in the review
   notes. (The API key it prints, which does not expire, is a fallback.)
2. **Create provider keys just for review**, on free plans: Finnhub, Tiingo and Alpha
   Vantage, under a separate email so they can be closed afterwards. Create an Anthropic
   key in its own workspace with a monthly spend limit of a few dollars.
3. **Fill the account** by signing in with its key once: add the review keys in Settings,
   enter a small sample portfolio (for example SPY 10, AAPL 5, MSFT 5, TLT 10), run and
   save one built-in backtest and one Python backtest, and acknowledge the advice notice.
   The keys stay in that browser only; the reviewer pastes them in again from the notes.
4. **Do not turn on the nightly refresh** for this account, so no keys are stored on the
   server.
5. **After each review** (approval or rejection): check that the account still works, and
   rotate the review keys if they appear anywhere but App Store Connect.
6. **When review is no longer needed:** delete the account
   (`... users delete app-review@<your-domain> --yes`) and revoke the review keys at each
   provider.
