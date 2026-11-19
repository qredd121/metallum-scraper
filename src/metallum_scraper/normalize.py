"""Rebuild normalized tables from the raw page cache."""

from collections import Counter

from loguru import logger

from .crawl import band_index_row
from .endpoints import advanced_filters, classify, entity_id
from .parsers import advanced_layout, parse_album, parse_artist, parse_band, parse_band_rows, parse_discography, parse_listing
from .parsers.listing import LETTER_LAYOUT
from .store import Page, Store

Rows = dict[str, list[dict]]


def band_rows(page: Page) -> Rows:
    b = parse_band(page.text, page.url)
    if b is None:
        return {}
    band = {k: getattr(b, k) for k in (
        "id", "name", "country", "country_code", "location", "status", "formed_raw", "formed_year",
        "years_active_raw", "genre_raw", "themes_raw", "label_id", "label_name", "label_kind",
        "added_on", "modified_on",
    )} | {"scraped_at": page.fetched_at}
    return {
        "bands": [band],
        "band_genres": [
            {"band_id": b.id, "position": g.position, "genre_token": g.token, "period": g.period} for g in b.genres
        ],
        "band_members": [
            {
                "band_id": b.id,
                "artist_id": m.artist_id,
                "artist_name": m.name,
                "lineup_type": m.lineup_type,
                "roles_raw": m.roles_raw,
                "years_raw": m.years_raw,
            }
            for m in b.members
        ],
    }


def discography_rows(page: Page) -> Rows:
    band_id = entity_id(page.url)
    return {
        "discography": [
            {
                "band_id": band_id,
                "album_id": r.album_id,
                "name": r.name,
                "type": r.type,
                "year": r.year,
                "reviews_raw": r.reviews_raw,
                "review_count": r.review_count,
                "review_avg": r.review_avg,
            }
            for r in parse_discography(page.text)
        ]
    }


def album_rows(page: Page) -> Rows:
    a = parse_album(page.text, page.url)
    if a is None:
        return {}
    album = {k: getattr(a, k) for k in (
        "id", "name", "type", "release_date_raw", "release_date", "label_id", "label_name", "format",
        "catalog_id", "version_desc", "reviews_raw", "review_count", "review_avg",
    )} | {"scraped_at": page.fetched_at}
    return {
        "albums": [album],
        "album_bands": [
            {"album_id": a.id, "band_id": b.band_id, "band_name": b.name, "position": i}
            for i, b in enumerate(a.bands)
        ],
        "tracks": [
            {
                "id": t.id,
                "album_id": a.id,
                "number": t.number,
                "disc": t.disc,
                "side": t.side,
                "title": t.title,
                "duration_raw": t.duration_raw,
                "duration_s": t.duration_s,
                "has_lyrics": int(t.has_lyrics),
                "lyrics_note": t.lyrics_note,
            }
            for t in a.tracks
        ],
        "album_lineup": [
            {
                "album_id": a.id,
                "band_id": e.band_id,
                "artist_id": e.artist_id,
                "artist_name": e.artist_name,
                "section": e.section,
                "role_raw": e.role_raw,
            }
            for e in a.lineup
        ],
    }


def artist_rows(page: Page) -> Rows:
    a = parse_artist(page.text, page.url)
    if a is None:
        return {}
    artist = {k: getattr(a, k) for k in (
        "id", "name", "real_name", "gender", "country", "country_code", "age_raw", "birth_date_raw",
        "place_of_birth",
    )} | {"scraped_at": page.fetched_at}
    return {
        "artists": [artist],
        "artist_bands": [
            {
                "artist_id": a.id,
                "band_id": m.band_id,
                "band_name": m.band_name,
                "membership_type": m.membership_type,
                "roles_raw": m.roles_raw,
                "years_raw": m.years_raw,
            }
            for m in a.memberships
        ],
        "artist_albums": [
            {
                "artist_id": a.id,
                "band_id": m.band_id,
                "membership_type": m.membership_type,
                "album_id": al.album_id,
                "album_name": al.album_name,
                "year": al.year,
                "role_raw": al.role_raw,
            }
            for m in a.memberships
            for al in m.albums
        ],
    }


def listing_rows(page: Page) -> Rows:
    kind = classify(page.url)
    columns = advanced_layout(advanced_filters(page.url)) if kind == "search" else LETTER_LAYOUT
    bands = parse_band_rows(parse_listing(page.text).rows, columns)
    return {"band_index": [band_index_row(b, page.url) for b in bands]}


BUILDERS = {
    "band": band_rows,
    "discography": discography_rows,
    "album": album_rows,
    "artist": artist_rows,
    "search": listing_rows,
    "letter": listing_rows,
}


def rebuild(store: Store) -> Counter:
    stats: Counter = Counter()
    with store.transaction():
        store.reset_tables()
        store.db.execute("DELETE FROM band_index")
        for page in store.iter_pages(200):
            kind = classify(page.url)
            builder = BUILDERS.get(kind)
            if builder is None:
                stats["skipped"] += 1
                continue
            try:
                rows = builder(page)
            except Exception as e:
                logger.warning(f"parse failed for {page.url}: {type(e).__name__}: {e}")
                stats[f"{kind}:error"] += 1
                continue
            if not rows:
                stats[f"{kind}:invalid"] += 1
                continue
            for table, table_rows in rows.items():
                if table == "band_index":
                    store.upsert_band_index(table_rows)
                else:
                    store.insert(table, table_rows)
            stats[kind] += 1
            total = sum(stats.values())
            if total % 5000 == 0:
                logger.info(f"parsed {total} pages")
    return stats


def coverage(store: Store) -> str:
    """Share of non-null values per column, to spot parser gaps."""
    lines = []
    for table in ("bands", "albums", "artists", "tracks"):
        (n,) = store.db.execute(f"SELECT count(*) FROM {table}").fetchone()
        if not n:
            continue
        cols = [r[1] for r in store.db.execute(f"PRAGMA table_info({table})")]
        exprs = ", ".join(f"count({c})" for c in cols)
        counts = store.db.execute(f"SELECT {exprs} FROM {table}").fetchone()
        lines.append(f"{table} ({n} rows): " + ", ".join(f"{c} {k / n:.0%}" for c, k in zip(cols, counts)))
    return "\n".join(lines)
