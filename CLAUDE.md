## 4. Architecture Guidelines for Claude Code

When generating code and scaffolding this codebase, strictly adhere to the following principles:

1. **Strict Type Annotations & Pydantic Validation**:
   * All Python functions must include type hints (`typing` module / standard modern Python typing).
   * API endpoints must leverage Pydantic schemas for request validation and response serialization.

2. **Modular Architecture**:
   * Keep AI logic (`app/ai/`), financial ingestion (`app/data/`), statistical modeling (`app/stats/`), and API endpoints (`app/api/`) completely decoupled.
   * R code should reside in `app/stats/r_scripts/` with dedicated Python wrapper functions handling data conversion and execution.

3. **Asynchronous Execution**:
   * External market data calls (`yfinance`, FMP, SEC EDGAR) and Anthropic API requests must use async/await patterns (`httpx`, `AsyncAnthropic`) to maintain responsiveness.

4. **Error Handling & Resilience**:
   * Implement graceful fallback mechanisms for rate limits or network issues with financial APIs.
   * Validate market data inputs to handle missing values or non-trading day gaps before passing data into quantitative models.