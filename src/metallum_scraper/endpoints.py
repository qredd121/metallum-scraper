"""URL builders and URL classification for metal-archives.com."""

import re
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlsplit

BASE_URL = "https://www.metal-archives.com"

LETTERS = [*"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "NBR", "~"]

Kind = Literal["band", "discography", "album", "artist", "search", "letter"]
CRAWL_KINDS = ("band", "discography", "album", "artist")

# Advanced band search params, in the order the site's own form sends them.
ADVANCED_PARAMS = (
    "bandName",
    "genre",
    "country",
    "yearCreationFrom",
    "yearCreationTo",
    "bandNotes",
    "status",
    "themes",
    "location",
    "bandLabelName",
)
ADVANCED_PAGE = 200
LETTER_PAGE = 500


def band(band_id: int) -> str:
    # The ID route 404s for deleted bands; the slug form serves a placeholder page.
    return f"{BASE_URL}/band/view/id/{band_id}"


def discography(band_id: int) -> str:
    return f"{BASE_URL}/band/discography/id/{band_id}/tab/all"


def album(album_id: int) -> str:
    return f"{BASE_URL}/albums/_/_/{album_id}"


def artist(artist_id: int) -> str:
    return f"{BASE_URL}/artists/_/{artist_id}"


def advanced_bands(filters: dict[str, str], start: int, length: int = ADVANCED_PAGE) -> str:
    unknown = set(filters) - set(ADVANCED_PARAMS)
    if unknown:
        raise ValueError(f"unknown advanced search params: {sorted(unknown)}")
    params = {p: filters.get(p, "") for p in ADVANCED_PARAMS}
    params |= {"sEcho": 1, "iDisplayStart": start, "iDisplayLength": length}
    return f"{BASE_URL}/search/ajax-advanced/searching/bands?{urlencode(params)}"


def letter(letter: str, start: int, length: int = LETTER_PAGE) -> str:
    if letter == "#":
        letter = "NBR"
    if letter not in LETTERS:
        raise ValueError(f"unknown letter: {letter!r}")
    params = {"sEcho": 1, "iDisplayStart": start, "iDisplayLength": length}
    return f"{BASE_URL}/browse/ajax-letter/l/{letter}/json/1?{urlencode(params)}"


def advanced_filters(url: str) -> dict[str, str]:
    """Recover the non-empty filters from an advanced search URL."""
    qs = parse_qs(urlsplit(url).query)
    return {p: qs[p][0] for p in ADVANCED_PARAMS if qs.get(p, [""])[0]}


_ID_RE = re.compile(r"/id/(\d+)|/(\d+)/?$")


def entity_id(url: str | None) -> int | None:
    """Numeric ID of an entity URL, e.g. ``.../bands/Darkthrone/146`` -> 146."""
    if not url:
        return None
    m = _ID_RE.search(url.split("#")[0].split("?")[0])
    return int(m.group(1) or m.group(2)) if m else None


_PATTERNS: list[tuple[Kind, re.Pattern]] = [
    ("discography", re.compile(r"/band/discography/id/\d+")),
    ("band", re.compile(r"/(band/view/id|bands/[^/]*)/\d+/?$")),
    ("album", re.compile(r"/albums/[^/]*/[^/]*/\d+/?$")),
    ("artist", re.compile(r"/artists/[^/]*/\d+/?$")),
    ("search", re.compile(r"/search/ajax-advanced/searching/bands")),
    ("letter", re.compile(r"/browse/ajax-letter/l/")),
]


def classify(url: str) -> Kind | None:
    path = urlsplit(url).path
    for kind, pattern in _PATTERNS:
        if pattern.search(path):
            return kind
    return None
