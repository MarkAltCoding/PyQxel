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
* **AI Engine & NLP**: Anthropic API (`claude-3-5-sonnet`), LangChain / LlamaIndex, BeautifulSoup4, PyPDF
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
   git clone [https://github.com/MarkAltCoding/pyqxel.git](https://github.com/MarkAltCoding/pyqxel.git)
   cd pyqxel
