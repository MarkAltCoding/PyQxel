"""HTML documents rewritten as Markdown, the form Claude reads them in.

SEC filings and news articles arrive as HTML. Stripped to plain text, a filing's tables
run together into a line of numbers whose columns can no longer be told apart; as
Markdown, a table keeps its rows and columns, a heading stays a heading, and a list keeps
its items. :func:`html_to_markdown` does that rewriting for every document PyQxel hands
to Claude, keeping only what a reader would see.
"""

import re
import warnings

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from bs4.element import NavigableString

BLOCK_TAGS: list[str] = [
    "p",
    "div",
    "br",
    "tr",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "section",
    "article",
]
"""Elements that start a new line in a rendered document."""

HIDDEN_TAGS: list[str] = [
    "ix:header",
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "iframe",
    "form",
    "button",
]
"""Elements a reader never sees: an inline XBRL filing's machine-readable header, code,
and page furniture."""

_PREFIX_CELLS = frozenset({"$", "€", "£", "¥", "("})
"""Cells that belong to the number after them: SEC tables put the dollar sign of
"$ 1,234" in a cell of its own."""

_SUFFIX_CELLS = frozenset({")", "%", ")%", "%)", "pts", "bps"})
"""Cells that belong to the number before them, such as the parenthesis closing a
negative number."""


def html_to_markdown(html: str | Tag) -> str:
    """Return the visible text of ``html`` as Markdown.

    Tables become Markdown tables (a table used only to lay text out, with fewer than two
    rows of two or more filled cells, becomes plain lines), headings take ``#`` marks,
    and list items take ``-``. Filings often split a word across inline elements ("RIS"
    and "K FACTORS"), so line breaks are added only at block elements.
    """
    if isinstance(html, Tag):
        soup = html
    else:
        with warnings.catch_warnings():
            # Inline XBRL documents are XHTML; parsing them as HTML is intended.
            warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
            soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(HIDDEN_TAGS):
        tag.decompose()
    # The innermost tables first, so an outer table sees an inner one as text.
    for table in reversed(soup.find_all("table")):
        table.replace_with(NavigableString(f"\n\n{_table(table)}\n\n"))
    for level in range(1, 7):
        for tag in soup.find_all(f"h{level}"):
            text = _inline(tag)
            if text:
                tag.replace_with(NavigableString(f"\n\n{'#' * level} {text}\n\n"))
            else:
                tag.decompose()
    for tag in soup.find_all("li"):
        tag.insert(0, NavigableString("- "))
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.append("\n")
    text = soup.get_text()
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _inline(tag: Tag) -> str:
    """The text of ``tag`` on one line."""
    return re.sub(r"\s+", " ", tag.get_text(" ").replace("\xa0", " ")).strip()


def _cell_text(cell: Tag) -> str:
    """A table cell's text on one line, with its pipes escaped for Markdown."""
    return _inline(cell).replace("|", "\\|")


def _span(cell: Tag) -> int:
    """How many columns ``cell`` spans, at least one."""
    try:
        return max(1, min(int(str(cell.get("colspan") or 1)), 50))
    except ValueError:
        return 1


def _table(table: Tag) -> str:
    """Render one ``<table>`` as a Markdown table, or as plain lines when it only lays
    text out.

    SEC tables pad their numbers with empty spacer columns and put a dollar sign or a
    closing parenthesis in a cell of its own. Those are folded into the number, columns
    left empty in every row are dropped, and a header spanning several columns is placed
    over the column among them that holds the most figures.
    """
    rows: list[list[tuple[str, int]]] = []
    for tr in table.find_all("tr"):
        cells = [(_cell_text(cell), _span(cell)) for cell in tr.find_all(["td", "th"])]
        if any(text for text, _ in cells):
            rows.append(cells)
    if not rows:
        return ""
    if sum(1 for row in rows if sum(1 for text, _ in row if text) >= 2) < 2:
        return "\n".join(" ".join(text for text, _ in row if text) for row in rows)

    width = max(sum(span for _, span in row) for row in rows)
    filled = [0] * width
    for row in rows:
        column = 0
        for text, span in row:
            if text and span == 1:
                filled[column] += 1
            column += span

    grid: list[list[str]] = []
    for row in rows:
        line = [""] * width
        column = 0
        for text, span in row:
            if text:
                # A spanning cell goes over the column beneath it that holds the most.
                target = max(range(column, column + span), key=lambda c: (filled[c], -c))
                line[target] = text
            column += span
        grid.append(_fold_symbols(line))

    keep = [c for c in range(width) if any(line[c] for line in grid)]
    grid = [[line[c] for c in keep] for line in grid]
    header, *body = grid
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(" --- " for _ in header) + "|",
    ]
    lines.extend("| " + " | ".join(line) + " |" for line in body)
    return "\n".join(lines)


def _fold_symbols(line: list[str]) -> list[str]:
    """Join a row's lone currency signs, parentheses, and percent signs to their
    numbers, leaving the cells they stood in empty."""
    line = list(line)
    for index, text in enumerate(line):
        if text in _PREFIX_CELLS:
            following = next((i for i in range(index + 1, len(line)) if line[i]), None)
            if following is not None:
                line[following] = f"{text}{line[following]}"
                line[index] = ""
        elif text in _SUFFIX_CELLS:
            before = next((i for i in range(index - 1, -1, -1) if line[i]), None)
            if before is not None:
                line[before] = f"{line[before]}{text}"
                line[index] = ""
    return line
