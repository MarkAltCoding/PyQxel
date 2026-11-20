"""The IRS's yearly contribution limits for retirement and health savings accounts.

No provider PyQxel uses, and no IRS service, publishes these as data to fetch, so they
are kept here, from the IRS's own notices, and must be updated each year: the IRS
announces the next year's retirement plan and IRA limits in October or November (Notice
2026-66 for 2027) and the HSA limits in the spring (Revenue Procedure 2026-24 for 2027).
:func:`contribution_limits` says when the newest figures here are older than the year,
so the app warns that they may be outdated. Checked against both documents on
:data:`CHECKED_ON`.
"""

from datetime import date

from app.models.accounts import ContributionLimit, ContributionLimits, LimitYear

CHECKED_ON: date = date(2026, 11, 19)
"""When the figures below were last checked against the IRS's documents."""


def _year(
    year: int,
    *,
    ira: float,
    ira_catch_up: float,
    deferral: float,
    catch_up: float,
    catch_up_60_63: float,
    simple: float,
    simple_small_employer: float,
    simple_catch_up: float,
    simple_catch_up_60_63: float,
    total: float,
    compensation: float,
    hsa_self: float,
    hsa_family: float,
    roth_single: tuple[float, float],
    roth_joint: tuple[float, float],
    source: str,
    source_url: str,
) -> LimitYear:
    return LimitYear(
        year=year,
        limits=[
            ContributionLimit(
                account="Traditional and Roth IRAs (one limit shared by all of a person's IRAs)",
                limit=ira,
                catch_up_50=ira_catch_up,
                note="No more than the person's earned income for the year. A Roth IRA "
                "contribution shrinks at higher incomes (see the phase-out).",
            ),
            ContributionLimit(
                account="401(k), Roth 401(k), 403(b), and most 457(b) plans (the "
                "employee's own deferrals, one limit shared by traditional and Roth)",
                limit=deferral,
                catch_up_50=catch_up,
                catch_up_60_63=catch_up_60_63,
                note="Employer contributions come on top, up to the total limit below. "
                "Those who earned over the Roth catch-up threshold the year before must "
                "make catch-up contributions as Roth.",
            ),
            ContributionLimit(
                account="SIMPLE IRA and SIMPLE 401(k)",
                limit=simple,
                catch_up_50=simple_catch_up,
                catch_up_60_63=simple_catch_up_60_63,
                note=f"Some small employers' plans allow up to ${simple_small_employer:,.0f}.",
            ),
            ContributionLimit(
                account="SEP IRA and solo 401(k), all contributions together",
                limit=total,
                note="A SEP IRA takes at most 25% of compensation, counting compensation up "
                f"to ${compensation:,.0f}. A solo 401(k)'s employee deferrals count toward "
                "the 401(k) limit above as well.",
            ),
            ContributionLimit(
                account="Health savings account (HSA), self-only coverage",
                limit=hsa_self,
                catch_up_50=None,
                note="Those aged 55 or over may add $1,000, a figure set in law and not "
                "adjusted for inflation.",
            ),
            ContributionLimit(
                account="Health savings account (HSA), family coverage",
                limit=hsa_family,
                note="Those aged 55 or over may add $1,000.",
            ),
        ],
        roth_ira_phase_out_single=roth_single,
        roth_ira_phase_out_joint=roth_joint,
        source=source,
        source_url=source_url,
    )


LIMIT_YEARS: dict[int, LimitYear] = {
    2026: _year(
        2026,
        ira=7_500,
        ira_catch_up=1_100,
        deferral=24_500,
        catch_up=8_000,
        catch_up_60_63=11_250,
        simple=17_000,
        simple_small_employer=18_100,
        simple_catch_up=4_000,
        simple_catch_up_60_63=5_250,
        total=72_000,
        compensation=360_000,
        hsa_self=4_400,
        hsa_family=8_750,
        roth_single=(153_000, 168_000),
        roth_joint=(242_000, 252_000),
        source="IRS, as restated in Notice 2026-66 (2027 limits), which gives each 2026 "
        "figure it changes; HSA limits from Revenue Procedure 2025-19",
        source_url="https://www.irs.gov/pub/irs-drop/n-26-66.pdf",
    ),
    2027: _year(
        2027,
        ira=7_500,
        ira_catch_up=1_100,
        deferral=25_500,
        catch_up=8_500,
        catch_up_60_63=11_750,
        simple=17_500,
        simple_small_employer=19_100,
        simple_catch_up=4_000,
        simple_catch_up_60_63=5_250,
        total=75_000,
        compensation=375_000,
        hsa_self=4_500,
        hsa_family=9_000,
        roth_single=(158_000, 173_000),
        roth_joint=(250_000, 260_000),
        source="IRS Notice 2026-66 and news release IR-2026-136 (November 12, 2026); HSA "
        "limits from Revenue Procedure 2026-24",
        source_url="https://www.irs.gov/pub/irs-drop/n-26-66.pdf",
    ),
}
"""Each tax year's limits PyQxel knows, by year."""

NOTES: tuple[str, ...] = (
    "These are federal limits for each tax year. IRA and HSA contributions for a year may "
    "be made until that year's tax filing deadline, usually April 15 of the next year; "
    "contributions through an employer's plan must come out of that year's pay.",
    "The limits rise with inflation, in rounded steps, so they stay the same some years: "
    "the IRA limit moves in $500 steps (it was $6,500 in 2023, $7,000 in 2024 and 2025, and "
    "$7,500 in 2026 and 2027), the IRA catch-up in $100 steps since 2024, the 401(k) limit "
    "usually by $500 to $1,000 a year ($23,000 in 2024, $23,500 in 2025, $24,500 in 2026, "
    "$25,500 in 2027), and the HSA limits usually by $50 to $300 a year.",
    "The IRS announces the next year's retirement limits in October or November and the "
    "HSA limits in the spring.",
    "How much more you may put in also depends on what you have put in this year, in every "
    "account sharing the limit, and on your earned income: enter what is left as the "
    "account's room.",
)
"""How the limits work and how they usually change."""


def contribution_limits(today: date) -> ContributionLimits:
    """The limits of ``today``'s tax year and the next, when known.

    When ``today``'s year is later than any year here, the newest known year's figures are
    given with ``outdated`` set, since the real limits are likely higher.
    """
    newest = max(LIMIT_YEARS)
    current = LIMIT_YEARS.get(today.year) or LIMIT_YEARS[min(today.year, newest)]
    outdated = today.year > newest
    notes = list(NOTES)
    if outdated:
        notes.insert(
            0,
            f"These are the {newest} limits, the newest PyQxel has: the {today.year} limits "
            "are likely higher. Check irs.gov before contributing.",
        )
    return ContributionLimits(
        as_of=CHECKED_ON,
        current=current,
        next=LIMIT_YEARS.get(today.year + 1),
        outdated=outdated,
        notes=notes,
    )
