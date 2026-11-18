"""Seeding, queue worker and stage handlers."""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import datetime

from loguru import logger

from . import endpoints
from .client import BlockedError, Client, FetchError
from .parsers import (
    ListedBand,
    advanced_layout,
    parse_album,
    parse_artist,
    parse_band,
    parse_band_rows,
    parse_discography,
    parse_listing,
)
from .parsers.listing import LETTER_LAYOUT
from .store import Page, QueueItem, Store


class CrawlAborted(RuntimeError):
    pass


def fetch_patiently(client: Client, url: str, newer_than: datetime | None = None, max_blocks: int = 5) -> Page:
    """Cached page or fetch; waits out cooldowns, gives up after ``max_blocks`` blocks in a row."""
    for _ in range(max_blocks):
        try:
            return client.get(url, newer_than)
        except BlockedError as e:
            logger.warning(f"blocked: {e}")
            client.wait_unblocked()
    raise CrawlAborted(f"blocked {max_blocks} times in a row; check access in a browser and retry later")


# --- seeding -------------------------------------------------------------


def band_index_row(band: ListedBand, source_url: str) -> dict:
    row = asdict(band)
    row.pop("url")
    row.pop("extra")
    return row | {"source_url": source_url}


def seed_listing(
    store: Store,
    client: Client,
    url_for: Callable[[int], str],
    columns: list[str],
    enqueue: bool,
    newer_than: datetime | None = None,
) -> tuple[int, int]:
    """Page through a DataTables listing. Returns (bands seen, total reported)."""
    start, total, seen = 0, None, 0
    while True:
        url = url_for(start)
        page = fetch_patiently(client, url, newer_than)
        if page.status != 200:
            raise FetchError(f"listing returned {page.status}: {url}")
        listing = parse_listing(page.text)
        if total is None or listing.total:
            total = listing.total
        if not listing.rows:
            if start < total:
                raise FetchError(f"listing stopped at {start} of {total} rows: {url}")
            break
        bands = parse_band_rows(listing.rows, columns)
        with store.transaction():
            store.upsert_band_index(band_index_row(b, url) for b in bands)
            if enqueue:
                for b in bands:
                    store.enqueue(endpoints.band(b.band_id), "band", b.band_id, 0, url)
        seen += len(bands)
        start += len(listing.rows)
        logger.info(f"listing {start}/{total}")
        if start >= total:
            break
    return seen, total or 0


def seed_advanced(store: Store, client: Client, filters: dict[str, str], enqueue: bool = True, **kw) -> int:
    filters = {k: str(v) for k, v in filters.items() if v not in (None, "")}
    store.add_seed({"type": "advanced", "filters": filters, "enqueue": enqueue})
    seen, total = seed_listing(
        store, client, lambda s: endpoints.advanced_bands(filters, s), advanced_layout(filters), enqueue, **kw
    )
    logger.success(f"seeded {seen} bands (site reports {total})")
    return seen


def seed_letters(store: Store, client: Client, letters: Iterable[str], enqueue: bool = False, **kw) -> int:
    letters = list(letters)
    store.add_seed({"type": "letters", "letters": letters, "enqueue": enqueue})
    seen = 0
    for letter in letters:
        n, total = seed_listing(store, client, lambda s: endpoints.letter(letter, s), LETTER_LAYOUT, enqueue, **kw)
        logger.success(f"letter {letter}: {n} bands (site reports {total})")
        seen += n
    return seen


# --- stage handlers ------------------------------------------------------


def handle(store: Store, item: QueueItem, page: Page, expand_depth: int = 0) -> str:
    """Parse a fetched page and enqueue its children. Returns the new queue status."""
    if page.status == 404:
        return "gone"
    html, url, depth = page.text, item.url, item.depth

    if item.kind == "band":
        band = parse_band(html, url)
        if band is None or band.id is None:
            return "invalid"
        store.enqueue(endpoints.discography(band.id), "discography", band.id, depth, url)
        # Only lineups of seeded bands pull in artists.
        if depth == 0:
            for m in band.members:
                if m.artist_id is not None:
                    store.enqueue(endpoints.artist(m.artist_id), "artist", m.artist_id, depth, url)
    elif item.kind == "discography":
        for release in parse_discography(html):
            store.enqueue(endpoints.album(release.album_id), "album", release.album_id, depth, url)
    elif item.kind == "album":
        if parse_album(html, url) is None:
            return "invalid"
    elif item.kind == "artist":
        artist = parse_artist(html, url)
        if artist is None:
            return "invalid"
        if depth < expand_depth:
            for ms in artist.memberships:
                if ms.band_id is not None:
                    store.enqueue(endpoints.band(ms.band_id), "band", ms.band_id, depth + 1, url)
    else:
        raise ValueError(f"unknown kind {item.kind}")
    return "done"


def crawl(
    store: Store,
    client: Client,
    kinds: Iterable[str] = endpoints.CRAWL_KINDS,
    max_requests: int | None = None,
    refresh_older_than: datetime | None = None,
    expand_depth: int = 0,
    max_attempts: int = 3,
    max_blocks: int = 5,
) -> Counter:
    kinds = list(kinds)
    if refresh_older_than is not None:
        n = store.requeue_stale(kinds, refresh_older_than.isoformat(timespec="seconds"))
        logger.info(f"requeued {n} stale items")

    stats: Counter = Counter()
    budget_start = client.requests_made
    blocks = 0
    while True:
        item = store.next_item(kinds, max_attempts)
        if item is None:
            logger.success("queue empty")
            break
        page = client.cached(item.url, refresh_older_than)
        if page is None:
            if max_requests is not None and client.requests_made - budget_start >= max_requests:
                logger.info(f"request budget of {max_requests} reached")
                break
            try:
                page = client.fetch(item.url)
            except BlockedError as e:
                blocks += 1
                if blocks >= max_blocks:
                    raise CrawlAborted(f"blocked {blocks} times in a row; stopping") from e
                client.wait_unblocked()
                continue
            except FetchError as e:
                logger.warning(f"{item.kind} {item.entity_id}: {e}")
                store.mark(item.url, "error", str(e), attempt=True)
                stats["error"] += 1
                continue
            blocks = 0
            stats["fetched"] += 1
        else:
            stats["cached"] += 1

        try:
            with store.transaction():
                status = handle(store, item, page, expand_depth)
                store.mark(item.url, status)
        except Exception as e:
            logger.exception(f"handler failed for {item.url}")
            store.mark(item.url, "parse_error", f"{type(e).__name__}: {e}", attempt=True)
            status = "parse_error"
        stats[status] += 1
        logger.info(f"{status:<11} {item.kind:<11} {item.entity_id}  [{client.requests_made} req]")
    return stats


# --- status --------------------------------------------------------------


def status_report(store: Store, delay: float) -> str:
    db = store.db
    lines = []
    rows = db.execute("SELECT kind, status, count(*) FROM queue GROUP BY kind, status").fetchall()
    by_kind: dict[str, Counter] = {}
    for kind, status, n in rows:
        by_kind.setdefault(kind, Counter())[status] = n
    statuses = ["pending", "done", "gone", "error", "parse_error", "invalid"]
    lines.append(f"{'kind':<12}" + "".join(f"{s:>12}" for s in statuses) + f"{'total':>10}")
    for kind in endpoints.CRAWL_KINDS:
        c = by_kind.get(kind, Counter())
        lines.append(f"{kind:<12}" + "".join(f"{c[s]:>12}" for s in statuses) + f"{sum(c.values()):>10}")

    pages, size = db.execute("SELECT count(*), coalesce(sum(length(body)), 0) FROM pages").fetchone()
    lines.append(f"\npages cached: {pages} ({size / 1e6:.1f} MB compressed)")

    # Timestamps are UTC ISO strings with an offset; compare on the first 19 chars.
    last_hour = "substr(ts, 1, 19) >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-1 hour')"
    by_status = db.execute(
        f"SELECT coalesce(status, 'net'), count(*) FROM request_log WHERE {last_hour} GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    n_hour = sum(n for _, n in by_status)
    lines.append(f"requests last hour: {n_hour}  " + " ".join(f"{s}:{n}" for s, n in by_status))

    pending, = db.execute(
        "SELECT count(*) FROM queue q WHERE q.status IN ('pending', 'error') "
        "AND NOT EXISTS (SELECT 1 FROM pages p WHERE p.url = q.url)"
    ).fetchone()
    first, last = db.execute(
        f"SELECT julianday(min(substr(ts, 1, 19))), julianday(max(substr(ts, 1, 19))) "
        f"FROM request_log WHERE {last_hour}"
    ).fetchone()
    seconds = (last - first) * 86400 if first is not None else 0
    rate = n_hour / seconds if n_hour > 10 and seconds > 0 else 1 / delay
    eta_h = pending / rate / 3600 if rate else float("inf")
    lines.append(f"uncached queue items: {pending}, rate {rate * 3600:.0f} req/h, ETA {eta_h:.1f} h")
    lines.append("(ETA covers the current queue only; bands, discographies and artists enqueue more)")

    errors = db.execute(
        "SELECT last_error, count(*) FROM queue WHERE last_error IS NOT NULL "
        "GROUP BY last_error ORDER BY 2 DESC LIMIT 5"
    ).fetchall()
    if errors:
        lines.append("\ntop errors:")
        lines += [f"  {n:>6}  {e[:100]}" for e, n in errors]
    return "\n".join(lines)
