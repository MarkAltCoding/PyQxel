# AI Equity Research & Quantitative Investing Engine

An AI-native research and quantitative analysis platform designed for equity stock analysis, statistical modeling, factor research, and automated thesis generation. Built with a high-performance Python engine, specialized R statistical subroutines, and an API-first architecture designed for seamless deployment across macOS (MacBook) and iOS (iPhone).

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

* **Primary Backend Engine**: Python 3.11+ (FastAPI, Pandas, NumPy, Statsmodels, Scikit-Learn)
* **Statistical Modeling**: R 4.3+ (`rpy2` integration for GARCH modeling, time-series analysis, and econometric estimation)
* **AI Engine & NLP**: Anthropic API (Claude Sonnet 5, `claude-sonnet-5`), LangChain / LlamaIndex, BeautifulSoup4, PyPDF
* **Market Data Feeds**: `yfinance`, Financial Modeling Prep (FMP) / Alpha Vantage API, SEC EDGAR Scraper
* **Client Interface Target**: Cross-platform REST & WebSocket API servicing macOS and iOS clients (Swift/React Native compatible)

---

## Quickstart & Local Setup

### Prerequisites

1. **Python 3.11+** installed on your system.
2. **R 4.3+** installed (required for advanced statistical and econometric modules).
3. **Claude Code** CLI configured locally (`npm install -g @anthropic-ai/claude-code`).

### Installation

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/MarkAltCoding/PyQxel.git
   cd PyQxel
   ```

2. **Create a Virtual Environment and Install Dependencies**:
   ```bash
   python3.11 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
   Then install the R packages the statistical models use:
   ```bash
   Rscript -e 'install.packages("rugarch", repos = "https://cloud.r-project.org")'
   ```

3. **Configure Environment Variables**:
   ```bash
   cp .env.example .env
   ```
   Then fill in `.env`:
   * `ANTHROPIC_API_KEY` — from the [Anthropic Console](https://console.anthropic.com/).
   * `FINANCIAL_DATA_API_KEY` — optional; a [Financial Modeling Prep](https://site.financialmodelingprep.com/developer/docs) key, used as a fallback when `yfinance` fails.
   * `SEC_USER_AGENT` — optional; your name and contact email (e.g. `PyQxel jane@example.com`), which the [SEC requires](https://www.sec.gov/os/accessing-edgar-data) of automated clients. When set, AI analyses also read the Risk Factors and MD&A sections of the company's latest 10-K and 10-Q.
   * `R_HOME` — the output of `R RHOME` (e.g. `/Library/Frameworks/R.framework/Resources` on macOS).

4. **Run the API Server**:
   ```bash
   uvicorn app.main:app --reload
   ```
   The API is served at `http://127.0.0.1:8000`, with interactive docs at `http://127.0.0.1:8000/docs`. Check it with `curl http://127.0.0.1:8000/health`.

5. **Run the Tests**:
   ```bash
   pytest
   ```

## License

Distributed under the MIT License. See `LICENSE` for details.
