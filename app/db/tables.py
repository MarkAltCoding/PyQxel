"""ORM table definitions for the results database."""

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    LargeBinary,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def as_utc(moment: datetime) -> datetime:
    """Return a stored ``moment`` as aware UTC; SQLite hands back naive datetimes."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


class Base(DeclarativeBase):
    """Declarative base shared by every table."""


class UserRecord(Base):
    """A PyQxel account and the AI limits its owner set.

    Provider keys are not stored: browsers keep them and send them with each request.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ai_requests_per_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_requests_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yfinance_fallback: Mapped[bool] = mapped_column(Boolean, server_default=false())
    own_restricted_sources: Mapped[bool] = mapped_column(Boolean, server_default=false())
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """The Argon2id hash of the password the account signs in with, set only through a
    link emailed to its address; null when it signs in only with GitHub, Google, Apple,
    or an API key."""
    """Whether the admin's own requests use data sources not cleared for a paid product
    while the server does not; it applies only to the admin."""
    """Whether the admin turned on the yfinance fallback for their own requests; ignored
    for every other account."""


class ApiKeyRecord(Base):
    """A PyQxel API key, stored as the SHA-256 hash of the key."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_in_with: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """The provider a sign-in issued the key through, such as ``github``; null for keys
    created for scripts, which signing out leaves alone."""
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """When a key issued by signing in stops working; null for keys created for scripts."""


class IdentityRecord(Base):
    """A GitHub or Google account that signs in to a PyQxel account.

    ``subject`` is the provider's own, permanent ID for the person; ``email`` is the
    verified address they had when they last signed in. One PyQxel account may have an
    identity at each provider.
    """

    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_identities_provider"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppSignInRecord(Base):
    """A sign-in finished in the system browser for the native app, waiting to be
    collected by it.

    The browser hands the app a one-time code through a ``pyqxel://`` link, which another
    app could also claim, so the code alone is not enough: the app must also present the
    verifier whose SHA-256 ``challenge`` it sent when the sign-in started. Only the code's
    hash is stored, and a row is deleted when it is collected or soon after it expires.
    """

    __tablename__ = "app_sign_ins"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(16))
    challenge: Mapped[str] = mapped_column(String(64))
    destination: Mapped[str] = mapped_column(Text)
    """The page of the app to open once signed in."""
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailLinkRecord(Base):
    """A link emailed to an address to choose a password, waiting to be used.

    It is how an address is proven to belong to whoever signs in with it: an account is
    made, or a password added to the account already using the address, only when the
    link is used. Only the token's hash is stored. It is kept by address rather than by
    account, since a new address has no account yet; asking again replaces it, and using
    it deletes every link to that address.
    """

    __tablename__ = "email_links"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BacktestRecord(Base):
    """One stored backtest.

    The full response is kept as JSON in ``result``; the columns beside it copy the
    fields used to filter and list results, so listing does not parse every curve. A
    custom strategy's source and language are kept in ``code`` and ``language`` instead
    of ``result``.
    """

    __tablename__ = "backtests"
    __table_args__ = (Index("ix_backtests_symbol_created_at", "symbol", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    strategy: Mapped[str] = mapped_column(String(32))
    period: Mapped[str] = mapped_column(String(8))
    interval: Mapped[str] = mapped_column(String(8))
    total_return: Mapped[float] = mapped_column(Float)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float] = mapped_column(Float)
    benchmark_total_return: Mapped[float] = mapped_column(Float)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    """A custom strategy's source; null for built-in strategies."""
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """The language of ``code``; null for built-in strategies."""
    symbols: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    """Every symbol a strategy run on several allocated between; null for one symbol,
    which ``symbol`` holds alone."""


class AnalysisRecord(Base):
    """One AI-written analysis, kept so the same request can reuse it instead of paying again.

    ``requested_model`` and ``effort`` are the settings the report was asked for with;
    ``model`` is the one that wrote it, which differs after a fallback.
    ``context_version`` is the version of the data it was written from, so reports
    written from older, thinner contexts are not reused. The full response is kept as
    JSON in ``result``.
    """

    __tablename__ = "analyses"
    __table_args__ = (
        Index(
            "ix_analyses_reuse",
            "symbol",
            "kind",
            "period",
            "include_filings",
            "requested_model",
            "effort",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    period: Mapped[str] = mapped_column(String(8))
    include_filings: Mapped[bool] = mapped_column(Boolean)
    requested_model: Mapped[str] = mapped_column(String(64))
    effort: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    context_version: Mapped[int] = mapped_column(Integer, server_default="1")
    headline: Mapped[str] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)


class SimulationRecord(Base):
    """One stored Monte Carlo simulation.

    ``symbols`` holds the portfolio's symbols as ``,A,B,`` so one can be matched with a
    portable ``LIKE``. The full response is kept as JSON in ``result``.
    """

    __tablename__ = "simulations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbols: Mapped[str] = mapped_column(Text)
    horizon: Mapped[int] = mapped_column(Integer)
    paths: Mapped[int] = mapped_column(Integer)
    dependence: Mapped[str] = mapped_column(String(16))
    marginals: Mapped[str] = mapped_column(String(16))
    rebalancing: Mapped[str] = mapped_column(String(16), server_default="daily")
    expected_return: Mapped[float] = mapped_column(Float)
    probability_of_loss: Mapped[float] = mapped_column(Float)
    value_at_risk_95: Mapped[float] = mapped_column(Float)
    conditional_value_at_risk_95: Mapped[float] = mapped_column(Float)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)


class PortfolioRecord(Base):
    """The name an account gave one of its saved portfolios; unnamed ones have no row."""

    __tablename__ = "portfolios"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    portfolio: Mapped[int] = mapped_column(Integer, primary_key=True)
    """Which of the account's portfolios, from 1."""
    name: Mapped[str] = mapped_column(String(40))


class HoldingRecord(Base):
    """One position in one of an account's saved portfolios."""

    __tablename__ = "portfolio_holdings"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    portfolio: Mapped[int] = mapped_column(Integer, primary_key=True)
    """Which of the account's portfolios, from 1."""
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    shares: Mapped[float] = mapped_column(Float)
    position: Mapped[int] = mapped_column(Integer)
    """Order in which the account listed its holdings."""


class PortfolioAnalysisRecord(Base):
    """One AI-written review of an account's saved portfolio.

    ``holdings_key`` fingerprints the holdings it was written for, so a review is reused
    only while the portfolio is unchanged. The full response is kept as JSON in
    ``result``.
    """

    __tablename__ = "portfolio_analyses"
    __table_args__ = (Index("ix_portfolio_analyses_user_created_at", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    portfolio: Mapped[int] = mapped_column(Integer, server_default="1")
    """Which of the account's portfolios it reviews."""
    period: Mapped[str] = mapped_column(String(8))
    holdings_key: Mapped[str] = mapped_column(String(64))
    requested_model: Mapped[str] = mapped_column(String(64))
    effort: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    headline: Mapped[str] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)


class AssistantRequestRecord(Base):
    """One answer the strategy coding assistant wrote, kept only to count against the
    account's AI limits; the conversation itself is never stored."""

    __tablename__ = "assistant_requests"
    __table_args__ = (Index("ix_assistant_requests_user_created_at", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    model: Mapped[str] = mapped_column(String(64))


class ScreenedStockColumns:
    """The columns of a stock in a screened universe and its precomputed metrics, shared by
    the universe every account may see and each user's own.

    Metric columns are named as :data:`~app.models.screener.ScreenField` names them.
    """

    name: Mapped[str] = mapped_column(Text)
    exchange: Mapped[str] = mapped_column(String(16))
    asset_type: Mapped[str] = mapped_column(String(8), server_default="stock")
    """``stock`` or ``etf``; an ETF has no company figures, CIK, market cap, or rank."""
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cik: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    market_cap_source: Mapped[str] = mapped_column(String(16))
    market_cap_rank: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    fundamentals_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    price: Mapped[float] = mapped_column(Float)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_dollar_volume: Mapped[float] = mapped_column(Float)
    pe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_to_sales: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev_to_ebitda: Mapped[float | None] = mapped_column(Float, nullable=True)
    fcf_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_on_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    earnings_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_6m: Mapped[float | None] = mapped_column(Float, nullable=True)
    momentum_12_1: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta_market: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta_momentum: Mapped[float | None] = mapped_column(Float, nullable=True)
    factor_r_squared: Mapped[float | None] = mapped_column(Float, nullable=True)


class ScreenerStockRecord(ScreenedStockColumns, Base):
    """One stock in the shared screened universe and its precomputed metrics.

    The table holds one snapshot: each refresh replaces every row.
    """

    __tablename__ = "screener_stocks"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)


class UserScreenerStockRecord(ScreenedStockColumns, Base):
    """One stock in a user's own screened universe, built by their nightly refresh from
    their own keys and shown only to them.

    Each of the user's refreshes replaces all of their rows.
    """

    __tablename__ = "user_screener_stocks"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)


class ScreenerListingRecord(Base):
    """A common stock listed on a US exchange, from the SEC's ticker list.

    The shared first stage of the screener refresh stores them, public data every user's
    nightly refresh starts from. Each refresh replaces every row.
    """

    __tablename__ = "screener_listings"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    cik: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(Text)
    exchange: Mapped[str] = mapped_column(String(16))
    shares_outstanding: Mapped[float | None] = mapped_column(Float, nullable=True)
    """Shares outstanding from the company's SEC filings, kept only for a company with a
    single listed share class, whose market cap is then price times shares."""


class ScreenerCompanyRecord(Base):
    """A listed company's Standard Industrial Classification (SIC) code and its title, from
    its EDGAR submissions: public data, shared by every user's own screener, where the code
    gives the sector and its title the industry. Read again every 90 days."""

    __tablename__ = "screener_companies"

    cik: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    sic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sic_description: Mapped[str | None] = mapped_column(String(160), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScreenerFundamentalsRecord(Base):
    """A company's normalized financials, refreshed weekly for the screened universe.

    ``financials`` holds a :class:`~app.models.fundamentals.Financials` as JSON.
    """

    __tablename__ = "screener_fundamentals"

    cik: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latest_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    financials: Mapped[dict[str, Any]] = mapped_column(JSON)


class ScreenerRunRecord(Base):
    """One refresh of the screened universe, kept so its status and failures can be read."""

    __tablename__ = "screener_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    stocks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prices_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    fundamentals_refreshed: Mapped[bool] = mapped_column(Boolean, server_default=false())
    factor_data_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    licensed: Mapped[bool] = mapped_column(Boolean, server_default=false())
    """Whether the refresh used licensed market data alone. One that used yfinance and
    Nasdaq's feed is shown only to an account that may see unlicensed data."""


class NightlyRefreshRecord(Base):
    """A user's opt-in to the nightly refresh of their own screener.

    The refresh runs while no browser is open, so it needs the user's keys: ``credentials``
    holds them as JSON encrypted with :func:`app.core.security.encrypt_secret`. This is the
    only place PyQxel stores provider keys, and the row is deleted when the user opts out
    or deletes the account. ``refresh_time`` is a local ``HH:MM`` in ``time_zone``.
    """

    __tablename__ = "nightly_refreshes"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    refresh_time: Mapped[str] = mapped_column(String(5))
    time_zone: Mapped[str] = mapped_column(String(64))
    credentials: Mapped[str] = mapped_column(Text)
    providers: Mapped[list[str]] = mapped_column(JSON)
    """Which providers' keys ``credentials`` holds, so they can be listed unencrypted. The
    user chooses them: the refresh uses no other key."""
    price_history_stocks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Most stocks the refresh fetches price histories for, largest first; null for as
    many as the keys' plans allow, as before the user could choose."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    keys_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    etfs_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """When the refresh last checked every US ETF; on other nights it checks only the ETFs
    already in the user's universe."""
    universes: Mapped[list[str]] = mapped_column(JSON, server_default='["us_stocks", "us_etfs"]')
    """The sets each refresh rebuilds, combined (:data:`app.models.nightly.NightlyUniverse`)."""
    custom_symbols: Mapped[list[str]] = mapped_column(JSON, server_default="[]")
    """Tickers each refresh adds to those sets."""


class UserScreenerRunRecord(Base):
    """One nightly refresh of a user's own screener, kept so its status can be shown."""

    __tablename__ = "user_screener_runs"
    __table_args__ = (Index("ix_user_screener_runs_user_started_at", "user_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    stocks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Stocks in the universe the run built."""
    priced: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Of those, the stocks whose price-history columns the user's keys could fill."""
    prices_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict[str, int] | None] = mapped_column(JSON, nullable=True)
    """Requests the run sent to each provider, by provider name."""
    notes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    """What the run could not do as asked, and what it did instead."""
    cost_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    """What the run cost to compute on Cloud Run, in millionths of a dollar: its share of the
    job's time at the job's rates (:func:`app.jobs.nightly.run_cost_micros`)."""


class ProviderUsageRecord(Base):
    """Requests PyQxel sent on one provider key in one clock hour (UTC), and the bytes the
    answers took, for showing a plan's use where the provider reports none.

    ``key_id`` is a one-way hash of the key (:func:`app.data.usage.key_id`), never the key.
    Rows older than :data:`app.data.usage.KEEP_DAYS` are deleted.
    """

    __tablename__ = "provider_usage"

    key_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    requests: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(BigInteger)


class ProviderSymbolRecord(Base):
    """A symbol requested on one provider key in a month, for plans that limit the number
    of different symbols a month, such as Tiingo's free plan."""

    __tablename__ = "provider_usage_symbols"

    key_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)


class StoredHistoryRecord(Base):
    """A symbol's daily bars, as one provider served them on the user's own key, kept for
    that user alone so later pages and backtests reuse them instead of asking again.

    ``data`` is the frame as zlib-compressed JSON. ``requested_from`` is the first day
    asked for: bars starting later without a ``notice`` mean the listing began then.
    """

    __tablename__ = "stored_price_history"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    requested_from: Mapped[date] = mapped_column(Date)
    first_day: Mapped[date] = mapped_column(Date)
    last_day: Mapped[date] = mapped_column(Date)
    bars: Mapped[int] = mapped_column(Integer)
    notice: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[bytes] = mapped_column(LargeBinary)


class AppSettingRecord(Base):
    """A setting of the whole server, changed by its admin, such as whether data sources not
    cleared for commercial use are used at all. ``value`` holds JSON."""

    __tablename__ = "app_settings"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BillingAccountRecord(Base):
    """A user's billing with Stripe: their prepaid credit and usage subscription.

    PyQxel charges each user what their nightly refreshes cost it, at cost. Usage is paid
    from ``credit_micros`` first, and what credit does not cover goes to the subscription,
    which Stripe bills weekly, monthly, or yearly. Amounts are in millionths of a dollar,
    since one night's refresh can cost a fraction of a cent.
    """

    __tablename__ = "billing_accounts"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    stripe_customer_id: Mapped[str] = mapped_column(String(255), unique=True)
    credit_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    """Prepaid credit left; below zero when usage ran past it with no subscription to
    take the rest, which the next top-up repays."""
    subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscription_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    """Stripe's status for the subscription: ``active``, ``past_due``, ``canceled``, ..."""
    subscription_interval: Mapped[str | None] = mapped_column(String(8), nullable=True)
    """``week``, ``month``, or ``year``: how often Stripe bills the usage."""
    subscription_renews_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subscription_cancels: Mapped[bool] = mapped_column(Boolean, default=False)
    """Whether the subscription ends when the current period does."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BillingEntryRecord(Base):
    """One change to a user's billing: credit bought, refunded, or disputed, or one nightly
    refresh's cost, split between credit and the subscription.

    ``stripe_ref`` names the Stripe object or event an entry came from (a Checkout Session,
    a refund, a dispute), unique so that an event Stripe sends twice is applied once.
    """

    __tablename__ = "billing_entries"
    __table_args__ = (Index("ix_billing_entries_user_created_at", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    """``top_up``, ``usage``, ``refund``, or ``dispute``."""
    credit_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    """The change to the user's credit: positive for a top-up, negative for usage paid from
    it, a refund, or a dispute."""
    subscription_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    """Usage that credit did not cover, billed through the subscription."""
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """When ``subscription_micros`` was reported to Stripe's meter; null until then."""
    stripe_ref: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    payment_intent: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    """The payment a top-up was paid with, so its refund or dispute can find it."""
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
