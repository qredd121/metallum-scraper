# metallum-scraper

Scraper that builds a structured dataset from Encyclopaedia Metallum (https://www.metal-archives.com).
This repo is ONLY the scraper/parser/API layer. Data science work lives in a separate project
(e.g. `~/Code/metal-analysis`) that consumes the exported Parquet/CSV files.

## Conventions
- Python 3.12+, managed with `uv` (`uv add`, `uv run`). No pip.
- Short, imperative commit messages.
- Keep raw strings next to parsed fields. Judgement-heavy cleaning (genre taxonomy, role normalization,
  fuzzy dates) belongs in the analysis project, not here.

## Stack
- `curl_cffi` (Session(impersonate="chrome")) — needed to pass Cloudflare; plain `requests` gets 403s.
- `selectolax` or `bs4` + `lxml` for HTML parsing.
- `sqlite3` for raw page cache, crawl queue and normalized tables.
- `typer` CLI, `loguru` logging, `polars` for export, `pytest` for tests.

## Architecture (3 decoupled layers)

### 1. Fetch layer — `client.py`
- Single persistent curl_cffi session, serialized requests, default delay 3s + jitter (robots crawl-delay convention).
- 403 → close session, sleep random 10–60s, new session, warm up on site root, retry once;
  second 403 → global cooldown ~120s.
- 429 → honor `Retry-After` (cap 120s), else cooldown.
- 5xx / network error → retry with exponential backoff.
- 404 → mark entity gone (not an error).
- Identify with a UA that includes a contact address.
- Every response is stored raw (compressed) in `pages(url PK, status, fetched_at, body BLOB)`.
  Scrape once, parse many times; parser bugs never require refetching.
- Keep the client behind an interface so a Playwright fallback can be swapped in if curl_cffi stops working.

### 2. Crawl queue — `queue` table
- `queue(url PK, kind, status, attempts, last_error, priority, discovered_from)`.
- `kind` ∈ `band | discography | album | artist`.
- Stage handlers parse a fetched page and enqueue children. Fully resumable and idempotent.
- Skip already-fetched URLs; `--refresh-older-than` for selective refetch.
- `--max-requests N` for test runs.

### 3. Parse layer — `parsers/`
- Pure functions: HTML/JSON in → dataclasses out. No network access.
- Tested against saved HTML fixtures in `tests/fixtures/`.
- `metallum parse` rebuilds all normalized tables from the `pages` cache.

## Endpoints (base: https://www.metal-archives.com)

| Purpose | Endpoint | Format |
|---|---|---|
| Seed: filtered bands | `/search/ajax-advanced/searching/bands?bandName=&genre=&country=&yearCreationFrom=&yearCreationTo=&bandNotes=&status=&themes=&location=&bandLabelName=&sEcho=1&iDisplayStart=N&iDisplayLength=200` | JSON |
| Seed: all bands by letter | `/browse/ajax-letter/l/{A..Z,NBR,~}/json/1?sEcho=1&iDisplayStart=N&iDisplayLength=500` | JSON |
| Band page | `/bands/_/{id}` (canonical: `/band/view/id/{id}`) | HTML |
| Discography | `/band/discography/id/{id}/tab/all` | HTML table |
| Album | `/albums/_/_/{id}` | HTML |
| Artist | `/artists/_/{id}` | HTML |
| (later) Similar bands | `/band/ajax-recommendations/id/{id}` | HTML |
| (later) Band description | `/band/read-more/id/{id}` | HTML |
| (later) Lyrics | `/release/ajax-view-lyrics/id/{song_id}` | HTML |
| (later) Reviews by date | `/review/ajax-list-browse/by/date/selection/{YYYY-MM}/json/1` | JSON |
| (later) Recently added/modified | `/archives/ajax-band-list` | JSON |

JSON endpoints use DataTables format: `{"iTotalRecords": n, "aaData": [[cell_html, ...], ...]}`.
Band IDs = last path segment of the link URL.

### HTML selectors
- Band: name `h1.band_name`; stats `#band_stats` dt/dd pairs (country, location, status, formed in,
  years active, genre, themes, label); members `#band_tab_members_{current,past,live}` → `tr.lineupRow`,
  followed by `tr.lineupBandsRow` with "see also" band links.
- Album: `#album_info` dt/dd (type, release date, catalog id, label, format, reviews);
  tracklist `table.table_lyrics` rows `tr.even, tr.odd` (number, title, duration, song id/lyrics link);
  lineup `#album_members_lineup .lineupTable tr` (split albums: band header rows separate lineups).
- Artist: `#member_info` dt/dd (real name, age/birth, place of birth, gender, country);
  membership tabs (active, past, live, guest, misc).
- Discography: `<tr>` rows → album link, type, year, reviews "count (avg%)".

### Gotchas
- Advanced search column layout depends on supplied filters: base columns are name, genre, country —
  but when `country` is filtered, the 3rd column becomes **location**. Extra columns are appended for
  `location`, `themes`, `bandLabelName` filters (in that order). Parse by computed layout, not fixed index.
- Listing endpoints may floor `iDisplayStart` to their own block size and ignore `iDisplayLength`.
  Advance pagination by number of rows actually received; error if a page before `iTotalRecords` is empty.
- JSON responses are sometimes wrapped in HTML (`<pre>` or `<body>`); unwrap before `json.loads`.
- Cloudflare challenges are the #1 failure mode of existing scrapers.

## Seeding & scope
Slice is chosen at runtime via CLI:
```
metallum seed --genre "black metal" --country NO --year-from 1990 --year-to 1999
metallum seed --letters all            # shallow list of all ~200k bands (~400 requests)
metallum crawl --kinds band,discography,album,artist [--max-requests N]
metallum status                        # queue progress, ETA, error counts
metallum parse                         # rebuild normalized tables from raw cache
metallum export --format parquet --out ../metal-analysis/data/raw
```
- Artists are fetched only if they appear in a seeded band's lineup.
- Artist "see also" bands are recorded as edges, NOT crawled, unless `--expand-depth 1`.
- Budget: ~10–15 requests per band → ~1k bands ≈ 3–4h, ~10k bands ≈ 1.5 days at 3s/request.
- Site scale (stats page): ~200k bands, ~710k albums, ~4.4M songs, ~136k reviews.

## Schema (normalized tables)
- `bands(id, name, country, location, status, formed_year, years_active_raw, genre_raw, themes_raw, label_id, label_name, added_on, modified_on, scraped_at)`
- `band_genres(band_id, genre_token)` — tokenized from e.g. "Black/Death Metal (early); Melodic Death Metal (later)"
- `albums(id, name, type, release_date_raw, release_date, label_id, label_name, format, catalog_id, review_count, review_avg)`
- `album_bands(album_id, band_id)` — split albums have multiple bands
- `tracks(id, album_id, number, disc, title, duration_s, has_lyrics)`
- `artists(id, name, real_name, gender, country, birth_date_raw, place_of_birth)`
- `band_members(band_id, artist_id, lineup_type, roles_raw, years_raw)` — lineup_type ∈ current/past/live
- `album_lineup(album_id, band_id, artist_id, role_raw)`
- `artist_bands(artist_id, band_id, membership_type)` — edges incl. out-of-slice bands
- Infra: `pages`, `queue`, `seeds(seed_id, params_json, created_at)`

## Repo layout
```
metallum-scraper/
  pyproject.toml
  src/metallum_scraper/
    client.py        # rate-limited curl_cffi client + raw page cache writes
    store.py         # sqlite schema/migrations, pages, queue, tables
    endpoints.py     # URL builders
    parsers/{listing,band,discography,album,artist}.py
    crawl.py         # seeding, queue worker + stage handlers, status
    normalize.py     # pages cache -> normalized tables (`metallum parse`)
    export.py
    cli.py
  tests/fixtures/synthetic_*.html   # hand-written; real_*.html are gitignored
  tests/test_{parsers,client,crawl,real_fixtures}.py
  data/              # gitignored: metallum.sqlite, exports
```

## Milestones
1. [ ] Client + raw cache: fetch one band page through Cloudflare, store raw.
2. [ ] Seeding: advanced search + letter listing; verify pagination on a small filter.
3. [ ] Parsers: band, discography, album, artist — each with fixtures + tests.
4. [ ] Crawler: queue, resume, `status` with ETA, `--max-requests`.
5. [ ] Normalized tables + `parse` and `export` commands.
6. [ ] Pilot: ~200-band slice; check field coverage and requests/band; then choose the real slice.

Later / optional: similar bands graph, band descriptions, reviews (list + full text), labels, lyrics.

## Ethics / constraints
- No official API or dump exists; this is HTML/AJAX scraping.
- robots.txt: `User-agent: * Allow: /`, `Content-Signal: search=yes, ai-train=no`. AI agents/fetchers are
  disallowed — so coding agents must NOT fetch metal-archives.com directly; the human runs the scraper.
- Be polite: 1 concurrent request, ≥3s delay, cache everything, never re-fetch needlessly.
- Don't redistribute raw pages or review text; don't use review text for model training.

## References
- pymetallum (current, best reference for client + parsers): https://github.com/gabriel-jung/pymetallum
  — see `src/pymetallum/core/{client,api,parsers}.py`
- python-metallum: https://github.com/lcharlick/python-metallum
- enmet: https://pypi.org/project/enmet/
- z3c0 bulk ETL (bands → albums → tracks): https://github.com/z3c0/encyclopaedia_metallum
- Review scraper (review endpoints): https://github.com/jonchar/ma-scraper
- Existing datasets: https://www.kaggle.com/datasets/zhangjuefei/death-metal,
  https://huggingface.co/datasets/LeData/metal-archives-bands,
  https://huggingface.co/datasets/Jarbas/metal-archives-tracks
