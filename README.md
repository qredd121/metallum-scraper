# metallum-scraper

Polite scraper for Encyclopaedia Metallum → SQLite → Parquet/CSV. See `AGENTS.md` for design.

```sh
export METALLUM_CONTACT=you@example.com     # sent in User-Agent and From header (required)

# Milestone 1: one band through Cloudflare into the raw cache
uv run metallum fetch 146
uv run metallum fixture https://www.metal-archives.com/band/view/id/146 real_band_darkthrone

# Seed + crawl a slice
uv run metallum seed --genre "black metal" --country NO --year-from 1990 --year-to 1991
uv run metallum crawl --max-requests 50
uv run metallum status

# Rebuild tables from cache (no network) and export
uv run metallum parse
uv run metallum export --format parquet --out ../metal-analysis/data/raw
```

- Everything lives in `data/metallum.sqlite` (override with `--db` / `METALLUM_DB`); logs in `data/metallum.log`.
- `crawl` is resumable; cached pages never cost a request. `--refresh-older-than 30d` refetches stale pages.
- `seed --letters all` builds `band_index` (~400 requests) without queueing; add `--enqueue` to crawl them.
- `retry-errors` puts failed items back in the queue.

## Tests

```sh
uv run pytest
```

Parser tests use synthetic fixtures (`tests/fixtures/synthetic_*.html`). Save real pages from the cache with
`metallum fixture URL real_<band|discography|album|artist>_<name>` to run `tests/test_real_fixtures.py`;
these are gitignored and must not be redistributed.
