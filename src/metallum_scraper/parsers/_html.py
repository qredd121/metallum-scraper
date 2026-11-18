"""Small helpers shared by the HTML parsers."""

import re
from datetime import date

from selectolax.lexbor import LexborHTMLParser, LexborNode

from ..endpoints import entity_id

__all__ = ["LexborHTMLParser", "LexborNode", "entity_id"]


def clean(s: str | None) -> str | None:
    """Collapse whitespace; empty -> None."""
    if s is None:
        return None
    s = " ".join(s.split())
    return s or None


def text(node: LexborNode | None) -> str | None:
    """Whitespace-normalized text of a node (HTML comments are excluded)."""
    if node is None:
        return None
    return clean(node.text(deep=True, separator=""))


def html_text(fragment: str) -> str | None:
    """Text of an HTML fragment, e.g. a DataTables cell."""
    if not fragment:
        return None
    body = LexborHTMLParser(f"<div>{fragment}</div>").css_first("div")
    return text(body)


def first_link(node: LexborNode | None) -> LexborNode | None:
    return node.css_first("a[href]") if node is not None else None


def href(node: LexborNode | None) -> str | None:
    return node.attributes.get("href") if node is not None else None


def next_element(node: LexborNode) -> LexborNode | None:
    n = node.next
    while n is not None and n.tag in ("-text", "-comment"):
        n = n.next
    return n


def dl_pairs(container: LexborNode | None) -> dict[str, LexborNode]:
    """``<dt>Label:</dt><dd>..</dd>`` pairs keyed by lowercased label without colon."""
    pairs: dict[str, LexborNode] = {}
    if container is None:
        return pairs
    for dt in container.css("dt"):
        dd = next_element(dt)
        if dd is None or dd.tag != "dd":
            continue
        key = (text(dt) or "").rstrip(":").strip().lower()
        pairs[key] = dd
    return pairs


def country_code(node: LexborNode | None) -> str | None:
    """ISO code from a ``/lists/XX`` country link inside ``node``."""
    a = node.css_first('a[href*="/lists/"]') if node is not None else None
    if a is None:
        return None
    m = re.search(r"/lists/([A-Z0-9]{2,3})\b", href(a) or "")
    return m.group(1) if m else None


def to_int(s: str | None) -> int | None:
    if s is None:
        return None
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


_REVIEWS = re.compile(r"(\d+)(?:\s+reviews?)?\s*\((?:avg\.?\s*)?(\d+)%\)", re.I)


def parse_reviews(s: str | None) -> tuple[int | None, int | None]:
    """``"12 (82%)"`` or ``"23 reviews (avg. 85%)"`` -> (12, 82)."""
    if not s:
        return None, None
    m = _REVIEWS.search(s)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def parse_duration(s: str | None) -> int | None:
    if not s or not re.fullmatch(r"\d+(:\d{1,2}){1,2}", s):
        return None
    secs = 0
    for part in s.split(":"):
        secs = secs * 60 + int(part)
    return secs


_MONTHS = {
    m: i
    for i, m in enumerate(
        "january february march april may june july august september october november december".split(), 1
    )
}
_DATE_FULL = re.compile(r"^([A-Za-z]+) (\d{1,2})(?:st|nd|rd|th)?, (\d{4})$")
_DATE_MONTH = re.compile(r"^([A-Za-z]+),? (\d{4})$")


def parse_release_date(s: str | None) -> str | None:
    """Exact formats only: ``"February 17th, 1994"`` -> ``"1994-02-17"``, ``"1994-02"``, ``"1994"``."""
    if not s:
        return None
    s = s.strip()
    if m := _DATE_FULL.match(s):
        month = _MONTHS.get(m.group(1).lower())
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(2))).isoformat()
            except ValueError:
                return None
    if m := _DATE_MONTH.match(s):
        month = _MONTHS.get(m.group(1).lower())
        if month:
            return f"{m.group(2)}-{month:02d}"
    if re.fullmatch(r"\d{4}", s):
        return s
    return None


_PAREN = re.compile(r"\(([^()]*\d[^()]*|\?)\)")


def years_in_parens(s: str | None) -> str | None:
    """Parenthesized year ranges from a role string, joined with ``; ``.

    ``"Bass (1987-1989), Guitars (1989-present)"`` -> ``"1987-1989; 1989-present"``.
    """
    if not s:
        return None
    found = _PAREN.findall(s)
    return "; ".join(found) if found else None
