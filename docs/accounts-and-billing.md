# Accounts and Billing

## Accounts

* **Signing in**: with an email and password, or with GitHub or Google, on the website and in the apps ([setup](setup.md#signing-in-with-github-and-google)). Accounts are matched by verified email, so each way in reaches the same account, and no address joins one before it is proven by an emailed link. A sign-in key lasts 30 days and renews itself while in use; keys made for scripts (`POST /api/v1/me/api-keys`) never expire.
* **Your keys pay for your use.** Claude runs on your Anthropic key, and market data comes from your own Tiingo, FMP, EODHD, Finnhub, and Alpha Vantage keys, tried cheapest first for each kind of data ([data sources](data-sources.md)); free plans are enough to start, and Settings shows what each key covers and what a missing one turns off. The browser keeps the keys and sends them with each request (`X-Anthropic-Key`, `X-Tiingo-Key`, `X-FMP-Key`, `X-EODHD-Key`, `X-Alpha-Vantage-Key`, `X-Finnhub-Key`); the server uses them for that request only and scrubs them from its logs. The only keys it stores are those of a user who turns on work that runs while the browser is closed (the nightly refresh, scheduled picks), encrypted, and deleted when it is turned off.
* **Your work is yours**: backtests, reports, reviews, and picks are seen only by the account that made them, and never reused across accounts.
* **Limits**: you can cap your own AI requests an hour and a day in Settings; every account may make 120 requests a minute (`RATE_LIMIT_PER_MINUTE`).
* **Deleting an account** removes everything it holds, in Settings or with `DELETE /api/v1/me`.
* **Security**: pages run only the app's own scripts and talk only to its server. The apps keep keys in the iPhone's Keychain or a private file on the Mac, not web storage; the threat model is at the top of `web/src/lib/secureStorage.ts`.

## Paying for the nightly refresh

Using PyQxel is free. The one thing that costs its server real money is the [nightly refresh](models.md#each-users-own-screener) of your own screener, which runs on Cloud Run for minutes (the S&P 500, about a cent) to hours (every US stock and ETF, about 29 cents). You pay what it costs, with nothing added, through Stripe:

* **Prepaid credit** ($5 to $500), or a **usage subscription** billed weekly, monthly, or yearly. Stripe's fees are passed on at cost, and Settings shows what each choice adds.
* **No run goes past your credit.** Without a subscription, a refresh or a run of scheduled picks starts only when the credit covers what it is expected to cost, and one that reaches the credit is stopped there; the Screener, its Refresh Schedule tab, the picks schedule, and Billing warn first.
* **Claude's work is not billed by PyQxel**: Anthropic bills your API key, or your Claude plan for a prompt you paste into Claude yourself. Data providers bill their own paid plans.
* **Refunds, disputes, and tax**: the [terms of service](/terms) and [refund policy](/refunds) cover them; deleting an account refunds the credit left. Sales tax comes from Stripe Tax where PyQxel is registered, and payments from the EU and UK are not accepted.

Payments are made on the website; the App Store build shows billing without a way to pay. Setting up Stripe for a deployment is in [setup](setup.md#google-cloud-run).
