# PyQxel: AI-Powered Quantitative and Fundamental Equity Research

An AI-native research and quantitative analysis platform that helps investors choose the right securities. It combines equity analysis, statistical modeling, factor research, and automated thesis generation so an investor can see what drives a security's returns, how much risk it carries, and how it would behave alongside the rest of a portfolio before buying it. Built with a high-performance Python engine, specialized R statistical subroutines, and an API-first architecture designed for seamless deployment across macOS (MacBook) and iOS (iPhone).

**Author:** Mark Altreuter ([@MarkAltCoding](https://github.com/MarkAltCoding))  
**Institution:** University of Illinois Urbana-Champaign (UIUC), B.S. Computer Science  

---

## Key Features

* **AI-Assisted Fundamental Research**: Automated ingestion of financial filings, earnings call transcripts, and news feeds using LLM agents (Claude / Anthropic API) to synthesize actionable investment theses and downside risk factors.
* **Quantitative & Econometric Modeling**: Hybrid Python and R statistical architecture leveraging time-series analysis, GARCH models, multi-factor risk regression, and Monte Carlo portfolio simulation.
* **Automated Stock Screening & Backtesting**: Vectorized backtesting framework for custom long/short equity strategies, technical indicator analysis, and performance attribution (Sharpe, Sortino, Max Drawdown).
* **Cross-Platform API Architecture**: Lightweight, high-throughput RESTful and WebSocket API built with FastAPI, optimized for consumption by native macOS applications, iOS mobile apps, or web-based dashboards.

---

## Tech Stack & System Architecture

* **Primary Backend Engine**: Python 3.14+ (FastAPI, Pandas, NumPy, Statsmodels, Scikit-Learn)
* **Statistical Modeling**: R 4.3+ (`rpy2` integration for GARCH modeling, time-series analysis, and econometric estimation)
* **AI Engine & NLP**: Anthropic API (Claude Opus 5.5, `claude-opus-5-5`) through the official `anthropic` SDK, BeautifulSoup4 for SEC filing text
* **Market Data Feeds**: licensed providers on each user's own keys, tried cheapest first for each kind of data ([`docs/data-sources.md`](docs/data-sources.md)): Tiingo (US price history, intraday bars and quotes, on a free plan), Financial Modeling Prep (company profiles and quotes; on the server's own licensed plan for the shared screener), EODHD (indices and listings worldwide), Finnhub (live US quotes, company profiles, news, analyst ratings, earnings and insider trades), and Alpha Vantage (filling gaps); SEC EDGAR; the European Central Bank's reference rates for currencies; `yfinance` (development fallback)
* **Client Interface Target**: Cross-platform REST & WebSocket API servicing macOS and iOS clients (Swift/React Native compatible)
* **Web Frontend**: React and TypeScript (Vite), with ECharts for charts, served by the API at `/`

---

---

## Install the App

PyQxel runs in a web browser at the hosted site, and as a free app for Mac and iPhone that signs in to the same account.

* **Mac**: download the `.dmg` from the [latest release](https://github.com/MarkAltCoding/PyQxel/releases/latest), drag PyQxel to Applications, and allow it once in **System Settings → Privacy & Security**, since it is not signed with a paid Apple certificate. It updates itself.
* **iPhone**: not on the App Store yet; build it with Xcode and install it on your own phone.

Step-by-step instructions, uninstalling, building from source, and publishing a release are in [Installing the App](docs/install.md).

---

## Quickstart

```bash
git clone https://github.com/MarkAltCoding/PyQxel.git && cd PyQxel
python3.14 -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                                   # then fill it in
venv/bin/python -m app.jobs.users create you@example.com --claim-unowned
YFINANCE_FALLBACK=true venv/bin/uvicorn app.main:app --port 8000
cd web && npm ci && npm run dev                        # http://localhost:5173
```

Sign in with the API key the `create` command prints, then add your own market data and Anthropic keys in **Settings**. `scripts/check.sh` runs every check CI runs.

## Documentation

| Document | What it covers |
| --- | --- |
| [Local Setup and Deployment](docs/setup.md) | Prerequisites (Python, R, Node.js), environment variables, the API and its streams, storage and caching, tests, Docker, Google Cloud Run, and signing in with GitHub, Google, or an email |
| [Accounts and Billing](docs/accounts-and-billing.md) | API keys, provider keys and what each unlocks, limits, deleting an account, and paying for the nightly screener refresh at cost through Stripe |
| [Quantitative Models and Features](docs/models.md) | Fundamentals, news, analysts and insiders, markets, the screener, factor exposures, copulas, Monte Carlo simulation, AI portfolio reviews, backtesting, and using Claude outside the app |
| [Data Sources](docs/data-sources.md) | Which provider serves each kind of data, cheapest first, on free and paid plans |
| [App Store](docs/app-store.md) | What is in place for an App Store submission, and what is left |

---

## License

Copyright (c) 2026 Mark Altreuter. All rights reserved. See [`LICENSE`](LICENSE).

The code is public so that it can be read and evaluated, not reused. You may view it, and use a PyQxel instance I host through your own account, but you may not copy, modify, distribute, deploy or otherwise use the code, in whole or in part, without my written permission, which may require attribution and payment. To ask, contact me through [GitHub](https://github.com/MarkAltCoding).
