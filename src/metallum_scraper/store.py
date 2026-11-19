"""SQLite storage: raw page cache, crawl queue, seeds and normalized tables."""

import json
import sqlite3
import zlib
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

INFRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    status INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    body BLOB
);
CREATE TABLE IF NOT EXISTS queue (
    url TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    entity_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    depth INTEGER NOT NULL DEFAULT 0,
    discovered_from TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS queue_pick ON queue (status, kind, attempts, priority);
CREATE TABLE IF NOT EXISTS seeds (
    seed_id INTEGER PRIMARY KEY,
    params_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS request_log (
    ts TEXT NOT NULL,
    url TEXT NOT NULL,
    status INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS request_log_ts ON request_log (ts);
CREATE TABLE IF NOT EXISTS band_index (
    band_id INTEGER PRIMARY KEY,
    name TEXT,
    genre_raw TEXT,
    country TEXT,
    location TEXT,
    themes_raw TEXT,
    label_name TEXT,
    status TEXT,
    source_url TEXT
);
"""

# Rebuilt from scratch by `metallum parse`.
TABLES_SCHEMA = """
CREATE TABLE bands (
    id INTEGER PRIMARY KEY,
    name TEXT,
    country TEXT,
    country_code TEXT,
    location TEXT,
    status TEXT,
    formed_raw TEXT,
    formed_year INTEGER,
    years_active_raw TEXT,
    genre_raw TEXT,
    themes_raw TEXT,
    label_id INTEGER,
    label_name TEXT,
    label_kind TEXT,
    added_on TEXT,
    modified_on TEXT,
    scraped_at TEXT
);
CREATE TABLE band_genres (
    band_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    genre_token TEXT NOT NULL,
    period TEXT
);
CREATE TABLE band_members (
    band_id INTEGER NOT NULL,
    artist_id INTEGER,
    artist_name TEXT,
    lineup_type TEXT NOT NULL,
    roles_raw TEXT,
    years_raw TEXT
);
CREATE TABLE discography (
    band_id INTEGER NOT NULL,
    album_id INTEGER,
    name TEXT,
    type TEXT,
    year INTEGER,
    reviews_raw TEXT,
    review_count INTEGER,
    review_avg INTEGER
);
CREATE TABLE albums (
    id INTEGER PRIMARY KEY,
    name TEXT,
    type TEXT,
    release_date_raw TEXT,
    release_date TEXT,
    label_id INTEGER,
    label_name TEXT,
    format TEXT,
    catalog_id TEXT,
    version_desc TEXT,
    reviews_raw TEXT,
    review_count INTEGER,
    review_avg INTEGER,
    scraped_at TEXT
);
CREATE TABLE album_bands (
    album_id INTEGER NOT NULL,
    band_id INTEGER,
    band_name TEXT,
    position INTEGER NOT NULL
);
CREATE TABLE tracks (
    id INTEGER,
    album_id INTEGER NOT NULL,
    number INTEGER,
    disc INTEGER,
    side TEXT,
    title TEXT,
    duration_raw TEXT,
    duration_s INTEGER,
    has_lyrics INTEGER,
    lyrics_note TEXT
);
CREATE TABLE album_lineup (
    album_id INTEGER NOT NULL,
    band_id INTEGER,
    artist_id INTEGER,
    artist_name TEXT,
    section TEXT,
    role_raw TEXT
);
CREATE TABLE artists (
    id INTEGER PRIMARY KEY,
    name TEXT,
    real_name TEXT,
    gender TEXT,
    country TEXT,
    country_code TEXT,
    age_raw TEXT,
    birth_date_raw TEXT,
    place_of_birth TEXT,
    scraped_at TEXT
);
CREATE TABLE artist_bands (
    artist_id INTEGER NOT NULL,
    band_id INTEGER,
    band_name TEXT,
    membership_type TEXT NOT NULL,
    roles_raw TEXT,
    years_raw TEXT
);
CREATE TABLE artist_albums (
    artist_id INTEGER NOT NULL,
    band_id INTEGER,
    membership_type TEXT NOT NULL,
    album_id INTEGER,
    album_name TEXT,
    year INTEGER,
    role_raw TEXT
);
"""

NORMALIZED_TABLES = (
    "bands",
    "band_genres",
    "band_members",
    "discography",
    "albums",
    "album_bands",
    "tracks",
    "album_lineup",
    "artists",
    "artist_bands",
    "artist_albums",
)
EXPORT_TABLES = ("band_index", *NORMALIZED_TABLES)

PRIORITY = {"band": 1, "discography": 2, "album": 3, "artist": 3}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Page:
    url: str
    status: int
    fetched_at: str
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class QueueItem:
    url: str
    kind: str
    entity_id: int | None
    attempts: int
    depth: int


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(INFRA_SCHEMA)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    # --- pages ---------------------------------------------------------

    def put_page(self, url: str, status: int, body: bytes) -> Page:
        page = Page(url, status, now(), body)
        self.db.execute(
            "INSERT OR REPLACE INTO pages (url, status, fetched_at, body) VALUES (?, ?, ?, ?)",
            (url, status, page.fetched_at, zlib.compress(body, 6)),
        )
        return page

    def get_page(self, url: str) -> Page | None:
        row = self.db.execute(
            "SELECT url, status, fetched_at, body FROM pages WHERE url = ?", (url,)
        ).fetchone()
        return _page(row) if row else None

    def iter_pages(self, status: int = 200) -> Iterator[Page]:
        # Separate connection so callers can write while iterating.
        reader = sqlite3.connect(self.path)
        try:
            cur = reader.execute(
                "SELECT url, status, fetched_at, body FROM pages WHERE status = ? ORDER BY rowid",
                (status,),
            )
            for row in cur:
                yield _page(row)
        finally:
            reader.close()

    def log_request(self, url: str, status: int | None, error: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO request_log (ts, url, status, error) VALUES (?, ?, ?, ?)",
            (now(), url, status, error),
        )

    # --- seeds & queue -------------------------------------------------

    def add_seed(self, params: dict) -> int:
        cur = self.db.execute(
            "INSERT INTO seeds (params_json, created_at) VALUES (?, ?)",
            (json.dumps(params, sort_keys=True), now()),
        )
        return cur.lastrowid

    def enqueue(
        self,
        url: str,
        kind: str,
        entity_id: int | None,
        depth: int = 0,
        discovered_from: str | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO queue (url, kind, entity_id, priority, depth, discovered_from, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (url) DO UPDATE SET depth = min(depth, excluded.depth)
            """,
            (url, kind, entity_id, PRIORITY.get(kind, 0), depth, discovered_from, now()),
        )

    def next_item(self, kinds: Iterable[str], max_attempts: int) -> QueueItem | None:
        kinds = list(kinds)
        marks = ",".join("?" * len(kinds))
        row = self.db.execute(
            f"""
            SELECT url, kind, entity_id, attempts, depth FROM queue
            WHERE status IN ('pending', 'error') AND attempts < ? AND kind IN ({marks})
            ORDER BY attempts, priority DESC, rowid
            LIMIT 1
            """,
            (max_attempts, *kinds),
        ).fetchone()
        return QueueItem(*row) if row else None

    def mark(self, url: str, status: str, error: str | None = None, attempt: bool = False) -> None:
        self.db.execute(
            "UPDATE queue SET status = ?, last_error = ?, attempts = attempts + ?, updated_at = ? "
            "WHERE url = ?",
            (status, error, int(attempt), now(), url),
        )

    def requeue_stale(self, kinds: Iterable[str], cutoff: str) -> int:
        kinds = list(kinds)
        marks = ",".join("?" * len(kinds))
        cur = self.db.execute(
            f"""
            UPDATE queue SET status = 'pending', attempts = 0, updated_at = ?
            WHERE kind IN ({marks}) AND status IN ('done', 'gone', 'invalid')
              AND url IN (SELECT url FROM pages WHERE fetched_at < ?)
            """,
            (now(), *kinds, cutoff),
        )
        return cur.rowcount

    def reset_errors(self) -> int:
        cur = self.db.execute(
            "UPDATE queue SET status = 'pending', attempts = 0 "
            "WHERE status IN ('error', 'parse_error', 'invalid')"
        )
        return cur.rowcount

    # --- band index (from listings) -----------------------------------

    def upsert_band_index(self, rows: Iterable[dict]) -> None:
        self.db.executemany(
            """
            INSERT INTO band_index
                (band_id, name, genre_raw, country, location, themes_raw, label_name, status, source_url)
            VALUES
                (:band_id, :name, :genre_raw, :country, :location, :themes_raw, :label_name, :status, :source_url)
            ON CONFLICT (band_id) DO UPDATE SET
                name = excluded.name,
                genre_raw = coalesce(excluded.genre_raw, genre_raw),
                country = coalesce(excluded.country, country),
                location = coalesce(excluded.location, location),
                themes_raw = coalesce(excluded.themes_raw, themes_raw),
                label_name = coalesce(excluded.label_name, label_name),
                status = coalesce(excluded.status, status),
                source_url = excluded.source_url
            """,
            list(rows),
        )

    # --- normalized tables --------------------------------------------

    def reset_tables(self) -> None:
        for table in NORMALIZED_TABLES:
            self.db.execute(f"DROP TABLE IF EXISTS {table}")
        # Not executescript(): that would commit an open transaction.
        for statement in TABLES_SCHEMA.split(";"):
            if statement.strip():
                self.db.execute(statement)

    def insert(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        cols = list(rows[0])
        self.db.executemany(
            f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(':' + c for c in cols)})",
            rows,
        )


def _page(row: tuple) -> Page:
    url, status, fetched_at, body = row
    return Page(url, status, fetched_at, zlib.decompress(body) if body else b"")
