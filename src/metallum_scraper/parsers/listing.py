"""DataTables JSON listings: advanced band search and letter browse."""

import json
import re
from dataclasses import dataclass, field

from ._html import LexborHTMLParser, entity_id, html_text


class ListingError(ValueError):
    pass


@dataclass
class Listing:
    total: int
    rows: list[list[str]]


@dataclass
class ListedBand:
    band_id: int
    name: str | None
    url: str
    genre_raw: str | None = None
    country: str | None = None
    location: str | None = None
    themes_raw: str | None = None
    label_name: str | None = None
    status: str | None = None
    extra: dict[str, str | None] = field(default_factory=dict)


def unwrap_json(text: str) -> dict:
    """Parse JSON that may come wrapped in ``<pre>`` or ``<body>``."""
    text = text.strip()
    candidates = [text]
    if not text.startswith(("{", "[")):
        # Unescaped JSON inside tags: slice out the object. Escaped: take the element's text.
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            candidates.append(text[start : end + 1])
        doc = LexborHTMLParser(text)
        node = doc.css_first("pre") or doc.body
        if node is not None:
            candidates.append(node.text(deep=True).strip())
    error: Exception | None = None
    for candidate in candidates:
        # The site occasionally emits raw control characters inside strings.
        for c in (candidate, re.sub(r"[\x00-\x1f]", " ", candidate)):
            try:
                return json.loads(c)
            except json.JSONDecodeError as e:
                error = e
    raise ListingError(f"not JSON: {error}")


def parse_listing(text: str) -> Listing:
    data = unwrap_json(text)
    if not isinstance(data, dict) or "aaData" not in data:
        raise ListingError("missing aaData")
    total = int(data.get("iTotalRecords") or data.get("iTotalDisplayRecords") or 0)
    return Listing(total, data["aaData"])


def advanced_layout(filters: dict[str, str]) -> list[str]:
    """Column names of the advanced band search table for the given (non-empty) filters."""
    columns = ["name", "genre_raw", "location" if filters.get("country") else "country"]
    for param, column in (("location", "location"), ("themes", "themes_raw"), ("bandLabelName", "label_name")):
        if filters.get(param) and column not in columns:
            columns.append(column)
    return columns


LETTER_LAYOUT = ["name", "country", "genre_raw", "status"]


def parse_band_rows(rows: list[list[str]], columns: list[str]) -> list[ListedBand]:
    bands = []
    for row in rows:
        if not row:
            continue
        link = LexborHTMLParser(f"<div>{row[0]}</div>").css_first("a[href]")
        url = link.attributes.get("href") if link is not None else None
        band_id = entity_id(url)
        if band_id is None:
            continue
        band = ListedBand(band_id=band_id, name=html_text(link.html), url=url)
        for i, column in enumerate(columns[1:], 1):
            value = html_text(row[i]) if i < len(row) else None
            setattr(band, column, value)
        for i in range(len(columns), len(row)):
            band.extra[f"col{i}"] = html_text(row[i])
        bands.append(band)
    return bands
