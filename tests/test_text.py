"""Writing that code assembles."""

from app.core.text import tidy_name


def test_names_in_capitals_are_put_in_normal_case_keeping_acronyms() -> None:
    assert tidy_name("MICROSOFT CORP") == "Microsoft Corp"
    assert tidy_name("BANK OF AMERICA CORP /DE/") == "Bank of America Corp"
    assert tidy_name("THE TRADE DESK INC") == "The Trade Desk Inc"
    assert tidy_name("SPDR S&P 500 ETF TRUST") == "SPDR S&P 500 ETF Trust"
    assert tidy_name("ALPHABET INC-CL A") == "Alphabet Inc-CL A"
    assert tidy_name("HSBC HOLDINGS PLC") == "HSBC Holdings PLC"
    assert tidy_name("AES CORP") == "AES Corp", "a short acronym stays"
    assert tidy_name("MCDONALDS CORP") == "McDonalds Corp"
    assert tidy_name("Apple Inc.") == "Apple Inc.", "a name in normal case is left alone"
