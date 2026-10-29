## 4. Architecture Guidelines for Claude Code

When generating code and scaffolding this codebase, strictly adhere to the following principles:

1. **Strict Type Annotations & Pydantic Validation**:
   * All Python functions must include type hints (`typing` module / standard modern Python typing).
   * API endpoints must leverage Pydantic schemas for request validation and response serialization.

2. **Modular Architecture**:
   * Keep AI logic (`app/ai/`), financial ingestion (`app/data/`), statistical modeling (`app/stats/`), and API endpoints (`app/api/`) completely decoupled.
   * R code should reside in `app/stats/r_scripts/` with dedicated Python wrapper functions handling data conversion and execution.

3. **Asynchronous Execution**:
   * External market data calls (`yfinance`, FMP, SEC EDGAR) and Anthropic API requests must use async/await patterns (`httpx2`, `AsyncAnthropic`) to maintain responsiveness.

4. **Error Handling & Resilience**:
   * Implement graceful fallback mechanisms for rate limits or network issues with financial APIs.
   * Validate market data inputs to handle missing values or non-trading day gaps before passing data into quantitative models.

5. **Explain Every Stat, Model and Graph**:
   * Every stat, model, chart, metric row or table column added to the web app gets an information icon (`<InfoTip topic=... />`, the circled "i") with a beginner-friendly explanation: what it measures, a rule-of-thumb range of good and worrying values where one applies, and any caveat.
   * Write the explanation as an entry in `web/src/lib/glossary.ts`, and reference it by key. `Stat` and `ChartCard` require an `info` prop, so the build fails until one is given.
   * Keep the "i" beside a heading (in a `heading-with-info` wrapper), not inside it, so the heading's accessible name stays clean.
