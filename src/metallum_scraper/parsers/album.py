"""Album page: ``/albums/_/_/{id}``."""

import re
from dataclasses import dataclass, field

from ._html import (
    LexborHTMLParser,
    LexborNode,
    dl_pairs,
    entity_id,
    first_link,
    href,
    parse_duration,
    parse_release_date,
    parse_reviews,
    text,
    to_int,
)

# Lineup tabs and the section name recorded for them.
LINEUP_TABS = (
    ("#album_members_lineup", "band"),
    ("#album_members_others", "guest"),
    ("#album_members_misc", "misc"),
)


@dataclass
class AlbumBand:
    band_id: int | None
    name: str | None


@dataclass
class Track:
    id: int | None
    number: int | None
    disc: int | None
    side: str | None
    title: str | None
    duration_raw: str | None
    duration_s: int | None
    has_lyrics: bool
    lyrics_note: str | None


@dataclass
class LineupEntry:
    band_id: int | None
    artist_id: int | None
    artist_name: str | None
    section: str | None
    role_raw: str | None


@dataclass
class Album:
    id: int | None
    name: str | None
    bands: list[AlbumBand] = field(default_factory=list)
    type: str | None = None
    release_date_raw: str | None = None
    release_date: str | None = None
    label_id: int | None = None
    label_name: str | None = None
    format: str | None = None
    catalog_id: str | None = None
    version_desc: str | None = None
    reviews_raw: str | None = None
    review_count: int | None = None
    review_avg: int | None = None
    tracks: list[Track] = field(default_factory=list)
    lineup: list[LineupEntry] = field(default_factory=list)


def parse_album(html: str, url: str | None = None) -> Album | None:
    doc = LexborHTMLParser(html)
    h1 = doc.css_first("h1.album_name")
    if h1 is None:
        return None
    album = Album(id=entity_id(url) or entity_id(href(first_link(h1))), name=text(h1))
    h2 = doc.css_first("h2.band_name")
    if h2 is not None:
        links = h2.css("a[href]")
        album.bands = [AlbumBand(entity_id(href(a)), text(a)) for a in links] or [AlbumBand(None, text(h2))]

    for key, dd in dl_pairs(doc.css_first("#album_info")).items():
        value = text(dd)
        if key == "type":
            album.type = value
        elif key == "release date":
            album.release_date_raw = value
            album.release_date = parse_release_date(value)
        elif key == "catalog id":
            album.catalog_id = None if value == "N/A" else value
        elif key == "label":
            album.label_name = value
            album.label_id = entity_id(href(first_link(dd)))
        elif key == "format":
            album.format = value
        elif key.startswith("version desc"):
            album.version_desc = value
        elif key == "reviews":
            album.reviews_raw = value
            album.review_count, album.review_avg = parse_reviews(value)

    album.tracks = _tracks(doc)
    album.lineup = _lineup(doc, album.bands)
    return album


def _tracks(doc: LexborHTMLParser) -> list[Track]:
    tracks = []
    disc, side = None, None
    for table in doc.css("table.table_lyrics"):
        for row in table.css("tr"):
            classes = row.attributes.get("class") or ""
            if "discRow" in classes:
                disc = to_int(text(row))
                side = None
                continue
            if "sideRow" in classes:
                side = text(row)
                continue
            if not re.search(r"\b(even|odd)\b", classes):
                continue
            cells = row.css("td")
            if len(cells) < 3:
                continue
            number = text(cells[0])
            if not number or not number.endswith("."):
                continue
            anchor = cells[0].css_first("a[name]")
            song_id = to_int(anchor.attributes.get("name")) if anchor is not None else None
            lyrics_cell = cells[3] if len(cells) > 3 else None
            has_lyrics = lyrics_cell is not None and lyrics_cell.css_first('a[id^="lyricsButton"]') is not None
            note = text(lyrics_cell.css_first("em")) if lyrics_cell is not None else None
            duration = text(cells[2])
            tracks.append(
                Track(
                    id=song_id,
                    number=to_int(number),
                    disc=disc,
                    side=side,
                    title=text(cells[1]),
                    duration_raw=duration,
                    duration_s=parse_duration(duration),
                    has_lyrics=has_lyrics,
                    lyrics_note=note,
                )
            )
    return tracks


def _lineup(doc: LexborHTMLParser, bands: list[AlbumBand]) -> list[LineupEntry]:
    entries: list[LineupEntry] = []
    for selector, section in LINEUP_TABS:
        tab = doc.css_first(selector)
        if tab is not None:
            entries += _lineup_table(tab, bands, section)
    if not entries and (tab := doc.css_first("#album_all_members_lineup")) is not None:
        entries = _lineup_table(tab, bands, None)
    return entries


def _lineup_table(tab: LexborNode, bands: list[AlbumBand], section: str | None) -> list[LineupEntry]:
    """Rows of a lineup tab. Non-member rows are split-album band headers or section headers."""
    by_name = {(b.name or "").lower(): b.band_id for b in bands}
    band_id = bands[0].band_id if len(bands) == 1 else None
    entries = []
    for row in tab.css(".lineupTable tr"):
        classes = row.attributes.get("class") or ""
        if "lineupRow" not in classes:
            label = text(row)
            if not label:
                continue
            link = row.css_first('a[href*="/bands/"]')
            if link is not None:
                band_id = entity_id(href(link))
            elif label.lower() in by_name:
                band_id = by_name[label.lower()]
            elif section is None or "lineupHeaders" in classes:
                section = label
            continue
        cells = row.css("td")
        if not cells:
            continue
        link = first_link(cells[0])
        entries.append(
            LineupEntry(
                band_id=band_id,
                artist_id=entity_id(href(link)),
                artist_name=text(link) or text(cells[0]),
                section=section,
                role_raw=text(cells[1]) if len(cells) > 1 else None,
            )
        )
    return entries
