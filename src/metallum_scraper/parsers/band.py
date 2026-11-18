"""Band page: ``/band/view/id/{id}``."""

import re
from dataclasses import dataclass, field

from ._html import (
    LexborHTMLParser,
    country_code,
    dl_pairs,
    entity_id,
    first_link,
    href,
    next_element,
    text,
    to_int,
    years_in_parens,
)

LINEUP_TYPES = ("current", "past", "live")


@dataclass
class SeeAlso:
    band_id: int | None
    name: str | None


@dataclass
class Member:
    artist_id: int | None
    name: str | None
    lineup_type: str
    roles_raw: str | None
    years_raw: str | None
    see_also: list[SeeAlso] = field(default_factory=list)


@dataclass
class GenreToken:
    position: int
    token: str
    period: str | None


@dataclass
class Band:
    id: int | None
    name: str | None
    country: str | None = None
    country_code: str | None = None
    location: str | None = None
    status: str | None = None
    formed_raw: str | None = None
    formed_year: int | None = None
    years_active_raw: str | None = None
    genre_raw: str | None = None
    themes_raw: str | None = None
    label_id: int | None = None
    label_name: str | None = None
    label_kind: str | None = None
    added_on: str | None = None
    modified_on: str | None = None
    genres: list[GenreToken] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)


def parse_band(html: str, url: str | None = None) -> Band | None:
    doc = LexborHTMLParser(html)
    h1 = doc.css_first("h1.band_name")
    if h1 is None:
        return None
    band = Band(id=entity_id(url) or entity_id(href(first_link(h1))), name=text(h1))

    stats = dl_pairs(doc.css_first("#band_stats"))
    for key, dd in stats.items():
        value = text(dd)
        if key.startswith("country"):
            band.country = value
            band.country_code = country_code(dd)
        elif key == "location":
            band.location = value
        elif key == "status":
            band.status = value
        elif key == "formed in":
            band.formed_raw = value
            band.formed_year = to_int(value) if value and re.fullmatch(r"\d{4}", value) else None
        elif key == "years active":
            band.years_active_raw = value
        elif key == "genre":
            band.genre_raw = value
        elif key.startswith("themes") or key.startswith("lyrical themes"):
            band.themes_raw = value
        elif key.endswith("label"):
            band.label_name = value
            band.label_kind = key.removesuffix("label").strip() or None
            band.label_id = entity_id(href(first_link(dd)))

    audit = text(doc.css_first("#auditTrail"))
    if audit:
        if m := re.search(r"Added on:\s*(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?)", audit):
            band.added_on = m.group(1)
        if m := re.search(r"Last modified on:\s*(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?)", audit):
            band.modified_on = m.group(1)

    band.genres = tokenize_genres(band.genre_raw)
    band.members = _members(doc)
    return band


def _members(doc: LexborHTMLParser) -> list[Member]:
    members = []
    for lineup_type in LINEUP_TYPES:
        tab = doc.css_first(f"#band_tab_members_{lineup_type}")
        if tab is None:
            continue
        for row in tab.css("tr.lineupRow"):
            cells = row.css("td")
            if not cells:
                continue
            link = first_link(cells[0])
            roles = text(cells[1]) if len(cells) > 1 else None
            member = Member(
                artist_id=entity_id(href(link)),
                name=text(link) or text(cells[0]),
                lineup_type=lineup_type,
                roles_raw=roles,
                years_raw=years_in_parens(roles),
            )
            sibling = next_element(row)
            if sibling is not None and "lineupBandsRow" in (sibling.attributes.get("class") or ""):
                member.see_also = [SeeAlso(entity_id(href(a)), text(a)) for a in sibling.css("a[href]")]
            members.append(member)
    return members


def _split_top_level(s: str, seps: str) -> list[str]:
    parts, depth, buf = [], 0, []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        if ch in seps and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def tokenize_genres(genre_raw: str | None) -> list[GenreToken]:
    """``"Black/Death Metal (early); Melodic Death Metal (later)"`` ->
    ``[("Black/Death Metal", "early"), ("Melodic Death Metal", "later")]``.

    Splits on top-level ``;`` and ``,`` only; the period in trailing parentheses is
    applied to every token of its ``;`` group. No taxonomy mapping here.
    """
    if not genre_raw:
        return []
    tokens: list[GenreToken] = []
    for group in _split_top_level(genre_raw, ";"):
        period = None
        if m := re.search(r"\(([^()]*)\)\s*$", group):
            period = m.group(1).strip() or None
            group = group[: m.start()].strip()
        for token in _split_top_level(group, ","):
            token = re.sub(r"\s+", " ", token).strip()
            if token:
                tokens.append(GenreToken(len(tokens), token, period))
    return tokens
