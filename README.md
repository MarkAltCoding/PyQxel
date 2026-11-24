# PyQxel

**Investment research for choosing what to buy, with Claude doing the reading.**

### ▶ Use it on the web: **[pyqxel-kha7vnmiiq-uc.a.run.app](https://pyqxel-kha7vnmiiq-uc.a.run.app)**

PyQxel runs in any browser, and as an app for [Mac](docs/install.md#mac-download-it) and [iPhone](docs/install.md#iphone-or-the-mac-app-from-source). All three sign in to the same account and show the same work. It is free to use; you bring your own data and Anthropic keys, and free plans are enough to start.

**Author:** Mark Altreuter ([@MarkAltCoding](https://github.com/MarkAltCoding)), University of Illinois Urbana-Champaign, B.S. Computer Science

---

## What it does

* **Stock pages**: prices, factor exposures, GARCH volatility, fundamentals and valuation from SEC filings, news, analysts' ratings, earnings, insiders' trades, and public records, with an AI investment thesis or risk report that reads the filings and the most important articles in full.
* **Portfolio**: up to five portfolios, Monte Carlo outcomes, how the holdings move together, and an AI review that writes the orders to rebalance, tailored to your risk tolerance, horizon, and accounts.
* **Screener and Claude's Picks**: filter a universe of US stocks and ETFs (your own, refreshed nightly on your keys), then have Claude pick from it for a holding period you choose, with the orders to place at a broker and a running record of how each list did.
* **Backtesting**: built-in strategies, or your own in Python or C++ run in the browser, with a coding assistant.
* **Markets**: FOMC meetings, earnings, IPOs, holidays, and market news.
* **Claude, your way**: every report runs on your Anthropic key, or as a prompt you paste into Claude yourself; each one records the model and effort that wrote it.

Every figure and chart has an "i" explaining it for a beginner. PyQxel is not investment advice.

## Documentation

| | |
| --- | --- |
| [Installing the app](docs/install.md) | Mac and iPhone, updates, and building from source |
| [Features and models](docs/models.md) | How each feature works, and what Claude is given |
| [Accounts and billing](docs/accounts-and-billing.md) | Keys, limits, and paying for the nightly refresh at cost |
| [Data sources](docs/data-sources.md) | Which provider serves what, on free and paid plans |
| [Local setup and deployment](docs/setup.md) | Running PyQxel yourself, and deploying it to Google Cloud Run |
| [App Store](docs/app-store.md) | What is ready for submission |

## Built with

Python 3.14 and FastAPI, with pandas, statsmodels, and `arch` for the models; React and TypeScript; Claude through the Anthropic SDK; Tauri for the Mac and iPhone apps; Postgres on Neon, on Google Cloud Run.

## License

Copyright (c) 2026 Mark Altreuter. All rights reserved. See [`LICENSE`](LICENSE).

The code is public so that it can be read and evaluated, not reused. You may view it, and use a PyQxel instance I host through your own account, but you may not copy, modify, distribute, deploy or otherwise use the code, in whole or in part, without my written permission, which may require attribution and payment. To ask, contact me through [GitHub](https://github.com/MarkAltCoding).
