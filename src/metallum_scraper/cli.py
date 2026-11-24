"""`metallum` command line."""

import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger

from . import endpoints
from .client import Client, CurlTransport
from .store import Store

app = typer.Typer(no_args_is_help=True, add_completion=False)

STATUS_CODES = {"active": "1", "on hold": "2", "split-up": "3", "unknown": "4", "changed name": "5", "disputed": "6"}


class State:
    db: Path = Path("data/metallum.sqlite")


state = State()

ContactOpt = Annotated[
    str | None, typer.Option(envvar="METALLUM_CONTACT", help="Contact email sent in User-Agent/From.")
]
DelayOpt = Annotated[float, typer.Option(min=3.0, help="Seconds between requests (plus jitter).")]
RefreshOpt = Annotated[
    str | None, typer.Option(help="Refetch cached pages older than this, e.g. 30d, 12h, 2w.")
]


@app.callback()
def main(
    db: Annotated[Path, typer.Option(envvar="METALLUM_DB", help="SQLite database path.")] = state.db,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    state.db = db
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")
    db.parent.mkdir(parents=True, exist_ok=True)
    logger.add(db.parent / "metallum.log", level="DEBUG", rotation="50 MB", retention=5)


def _client(store: Store, contact: str | None, delay: float) -> Client:
    if not contact or "@" not in contact:
        raise typer.BadParameter("set --contact or METALLUM_CONTACT to an email address", param_hint="--contact")
    return Client(store, CurlTransport(contact), delay=delay)


def _cutoff(spec: str | None) -> datetime | None:
    if not spec:
        return None
    m = re.fullmatch(r"(\d+)([hdw])", spec.strip())
    if not m:
        raise typer.BadParameter("use e.g. 12h, 30d, 2w", param_hint="--refresh-older-than")
    unit = {"h": "hours", "d": "days", "w": "weeks"}[m.group(2)]
    return datetime.now(UTC) - timedelta(**{unit: int(m.group(1))})


@app.command()
def seed(
    genre: str | None = None,
    country: Annotated[str | None, typer.Option(help="ISO country code, e.g. NO.")] = None,
    year_from: int | None = None,
    year_to: int | None = None,
    name: str | None = None,
    status: Annotated[str | None, typer.Option(help="active, on hold, split-up, unknown, changed name, disputed.")] = None,
    themes: str | None = None,
    location: str | None = None,
    label: str | None = None,
    notes: str | None = None,
    letters: Annotated[str | None, typer.Option(help="'all' or comma list, e.g. A,B,NBR,~.")] = None,
    enqueue: Annotated[
        bool | None, typer.Option(help="Queue bands for crawling. Default: yes for filters, no for letters.")
    ] = None,
    limit: Annotated[
        int | None, typer.Option(min=1, help="Queue at most N bands not already queued, then stop paging.")
    ] = None,
    refresh_older_than: RefreshOpt = None,
    contact: ContactOpt = None,
    delay: DelayOpt = 3.0,
) -> None:
    """Seed bands from the advanced search or the alphabetical listing."""
    from .crawl import seed_advanced, seed_letters

    filters = {
        "bandName": name,
        "genre": genre,
        "country": country.upper() if country else None,
        "yearCreationFrom": year_from,
        "yearCreationTo": year_to,
        "bandNotes": notes,
        "status": STATUS_CODES.get(status.lower(), status) if status else None,
        "themes": themes,
        "location": location,
        "bandLabelName": label,
    }
    has_filters = any(v not in (None, "") for v in filters.values())
    if bool(letters) == has_filters:
        raise typer.BadParameter("use either search filters or --letters")
    if letters and limit and enqueue is None:
        enqueue = True
    if limit and enqueue is False:
        raise typer.BadParameter("--limit needs --enqueue", param_hint="--limit")

    store = Store(state.db)
    client = _client(store, contact, delay)
    try:
        cutoff = _cutoff(refresh_older_than)
        if letters:
            chosen = endpoints.LETTERS if letters == "all" else [x.strip().upper() for x in letters.split(",")]
            seed_letters(store, client, chosen, enqueue=bool(enqueue), limit=limit, newer_than=cutoff)
        else:
            seed_advanced(store, client, filters, enqueue=enqueue is not False, limit=limit, newer_than=cutoff)
    finally:
        client.close()
        store.close()


@app.command()
def crawl(
    kinds: Annotated[str, typer.Option(help="Comma list of band,discography,album,artist.")] = ",".join(
        endpoints.CRAWL_KINDS
    ),
    max_requests: Annotated[int | None, typer.Option(help="Stop after N network requests.")] = None,
    refresh_older_than: RefreshOpt = None,
    expand_depth: Annotated[int, typer.Option(min=0, max=1, help="1 = also crawl artists' other bands.")] = 0,
    max_attempts: Annotated[int, typer.Option(min=1)] = 3,
    contact: ContactOpt = None,
    delay: DelayOpt = 3.0,
) -> None:
    """Work through the queue. Resumable; cached pages cost no requests."""
    from .crawl import CrawlAborted
    from .crawl import crawl as run

    chosen = [k.strip() for k in kinds.split(",") if k.strip()]
    if unknown := set(chosen) - set(endpoints.CRAWL_KINDS):
        raise typer.BadParameter(f"unknown kinds: {sorted(unknown)}", param_hint="--kinds")
    store = Store(state.db)
    client = _client(store, contact, delay)
    try:
        stats = run(store, client, chosen, max_requests, _cutoff(refresh_older_than), expand_depth, max_attempts)
        logger.info(f"done: {dict(stats)}")
    except CrawlAborted as e:
        logger.error(str(e))
        raise typer.Exit(2) from e
    except KeyboardInterrupt:
        logger.warning("interrupted; progress is saved, rerun to resume")
    finally:
        client.close()
        store.close()


@app.command()
def status(delay: DelayOpt = 3.0) -> None:
    """Queue progress, request rate, ETA and top errors."""
    from .crawl import status_report

    store = Store(state.db)
    typer.echo(status_report(store, delay + 0.75))
    store.close()


@app.command("retry-errors")
def retry_errors() -> None:
    """Reset failed/invalid queue items to pending."""
    store = Store(state.db)
    typer.echo(f"reset {store.reset_errors()} items")
    store.close()


@app.command()
def parse() -> None:
    """Rebuild all normalized tables from the raw page cache (no network)."""
    from .normalize import coverage, rebuild

    store = Store(state.db)
    stats = rebuild(store)
    typer.echo(" ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    typer.echo(coverage(store))
    store.close()


@app.command()
def export(
    format: Annotated[str, typer.Option("--format", help="parquet or csv.")] = "parquet",
    out: Annotated[Path, typer.Option(help="Output directory.")] = Path("data/export"),
) -> None:
    """Export normalized tables."""
    from .export import export as run

    if format not in ("parquet", "csv"):
        raise typer.BadParameter("parquet or csv", param_hint="--format")
    store = Store(state.db)
    run(store, out, format)
    store.close()


@app.command()
def fetch(
    url: str,
    force: Annotated[bool, typer.Option(help="Refetch even if cached.")] = False,
    contact: ContactOpt = None,
    delay: DelayOpt = 3.0,
) -> None:
    """Fetch a single URL (or band ID) into the cache and report status."""
    if url.isdigit():
        url = endpoints.band(int(url))
    store = Store(state.db)
    client = _client(store, contact, delay)
    try:
        page = client.fetch(url) if force else client.get(url)
        typer.echo(f"{page.status} {len(page.body)} bytes fetched_at={page.fetched_at} {url}")
    finally:
        client.close()
        store.close()


@app.command()
def fixture(
    url: str,
    name: str,
    out: Annotated[Path, typer.Option(help="Fixture directory.")] = Path("tests/fixtures"),
) -> None:
    """Copy a cached page into tests/fixtures/NAME (no network)."""
    store = Store(state.db)
    page = store.get_page(url)
    store.close()
    if page is None:
        raise typer.BadParameter(f"not cached: {url}")
    out.mkdir(parents=True, exist_ok=True)
    path = out / (name if "." in name else f"{name}.html")
    path.write_bytes(page.body)
    typer.echo(f"wrote {path} ({len(page.body)} bytes)")
