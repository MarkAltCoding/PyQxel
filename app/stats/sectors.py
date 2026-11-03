"""Sectors and industries from the SEC's Standard Industrial Classification (SIC) codes.

Every company filing with the SEC is assigned a four-digit SIC code, public in its EDGAR
submissions. The code's title is the industry, and ranges of codes map to the sector names
the screener uses (Nasdaq's: Technology, Health Care, Finance and so on). SIC is older and
coarser than commercial classifications, so a company can land in a different sector than
a data provider would put it in; the mapping follows where most companies of each code
belong.
"""

SECTOR_RANGES: tuple[tuple[int, int, str], ...] = (
    (100, 999, "Consumer Staples"),
    (1000, 1099, "Basic Materials"),
    (1200, 1399, "Energy"),
    (1400, 1499, "Basic Materials"),
    (1500, 1799, "Industrials"),
    (2000, 2199, "Consumer Staples"),
    (2200, 2399, "Consumer Discretionary"),
    (2400, 2499, "Basic Materials"),
    (2500, 2599, "Consumer Discretionary"),
    (2600, 2699, "Basic Materials"),
    (2700, 2799, "Consumer Discretionary"),
    (2800, 2829, "Basic Materials"),
    (2830, 2836, "Health Care"),
    (2840, 2844, "Consumer Staples"),
    (2845, 2899, "Basic Materials"),
    (2900, 2999, "Energy"),
    (3000, 3199, "Consumer Discretionary"),
    (3200, 3399, "Basic Materials"),
    (3400, 3569, "Industrials"),
    (3570, 3579, "Technology"),
    (3580, 3599, "Industrials"),
    (3600, 3629, "Technology"),
    (3630, 3639, "Consumer Discretionary"),
    (3640, 3699, "Technology"),
    (3710, 3716, "Consumer Discretionary"),
    (3700, 3799, "Industrials"),
    (3840, 3851, "Health Care"),
    (3800, 3899, "Technology"),
    (3900, 3999, "Consumer Discretionary"),
    (4000, 4799, "Industrials"),
    (4800, 4899, "Telecommunications"),
    (4950, 4959, "Industrials"),
    (4900, 4999, "Utilities"),
    (5120, 5122, "Health Care"),
    (5140, 5149, "Consumer Staples"),
    (5000, 5199, "Industrials"),
    (5400, 5499, "Consumer Staples"),
    (5910, 5912, "Consumer Staples"),
    (5200, 5999, "Consumer Discretionary"),
    (6500, 6553, "Real Estate"),
    (6798, 6798, "Real Estate"),
    (6000, 6799, "Finance"),
    (7370, 7379, "Technology"),
    (7300, 7399, "Industrials"),
    (7000, 7999, "Consumer Discretionary"),
    (8000, 8099, "Health Care"),
    (8100, 8999, "Industrials"),
)
"""SIC code ranges and their sectors. A narrower range listed before a wider one that
contains it wins."""


def sector_for_sic(sic: int | None) -> str | None:
    """The sector of SIC code ``sic``, or ``None`` for a code outside every range, such as
    a shell company's."""
    if sic is None:
        return None
    for low, high, sector in SECTOR_RANGES:
        if low <= sic <= high:
            return sector
    return None


def industry_name(description: str | None) -> str | None:
    """An SIC title as the screener shows industries: "SERVICES-PREPACKAGED SOFTWARE" reads
    "Services-Prepackaged Software"."""
    if not description:
        return None
    return description.strip().title()
