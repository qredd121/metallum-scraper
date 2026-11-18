"""Artist page: ``/artists/_/{id}``."""

import re
from dataclasses import dataclass, field

from ._html import LexborHTMLParser, clean, country_code, dl_pairs, entity_id, first_link, href, text

MEMBERSHIP_TABS = ("active", "past", "live", "guest", "misc")


@dataclass
class Membership:
    band_id: int | None
    band_name: str | None
    membership_type: str
    roles_raw: str | None
    years_raw: str | None


@dataclass
class Artist:
    id: int | None
    name: str | None
    real_name: str | None = None
    age_raw: str | None = None
    birth_date_raw: str | None = None
    place_of_birth: str | None = None
    country: str | None = None
    country_code: str | None = None
    gender: str | None = None
    memberships: list[Membership] = field(default_factory=list)


def parse_artist(html: str, url: str | None = None) -> Artist | None:
    doc = LexborHTMLParser(html)
    h1 = doc.css_first("h1.band_member_name")
    if h1 is None:
        return None
    artist = Artist(id=entity_id(url), name=text(h1))

    for key, dd in dl_pairs(doc.css_first("#member_info")).items():
        value = text(dd)
        if key.startswith("real"):
            artist.real_name = value
        elif key == "age":
            artist.age_raw = value
            if value and (m := re.search(r"born\s+(.+?)\)", value)):
                artist.birth_date_raw = m.group(1).strip()
        elif key == "place of birth":
            artist.place_of_birth = value
            link = dd.css_first('a[href*="/lists/"]')
            artist.country = text(link) if link is not None else None
            artist.country_code = country_code(dd)
        elif key == "gender":
            artist.gender = value

    for tab_name in MEMBERSHIP_TABS:
        tab = doc.css_first(f"#artist_tab_{tab_name}")
        if tab is None:
            continue
        for block in tab.css(".member_in_band"):
            h3 = block.css_first("h3.member_in_band_name")
            if h3 is None:
                continue
            link = first_link(h3)
            roles = text(block.css_first("p.member_in_band_role"))
            years = None
            if roles and (m := re.search(r"\(([^()]*)\)\s*$", roles)):
                years = clean(m.group(1))
            artist.memberships.append(
                Membership(
                    band_id=entity_id(href(link)),
                    band_name=text(link) or text(h3),
                    membership_type=tab_name,
                    roles_raw=roles,
                    years_raw=years,
                )
            )
    return artist
