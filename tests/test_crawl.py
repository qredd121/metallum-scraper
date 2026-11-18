import json
from pathlib import Path

import polars as pl

from conftest import ok
from metallum_scraper import endpoints
from metallum_scraper.crawl import crawl, seed_advanced, status_report
from metallum_scraper.export import export
from metallum_scraper.normalize import rebuild

FIXTURES = Path(__file__).parent / "fixtures"
FILTERS = {"genre": "black metal", "country": "NO"}


def search_page(rows, total):
    return ok(json.dumps({"iTotalRecords": total, "aaData": rows}))


def band_row(band_id, name):
    return [f'<a href="https://www.metal-archives.com/bands/{name}/{band_id}">{name}</a>', "Black Metal", "Oslo"]


def routes():
    fx = {p.stem: (FIXTURES / p.name).read_bytes() for p in FIXTURES.glob("synthetic_*.html")}
    return {
        # Server returns fewer rows than requested; pagination must advance by rows received.
        endpoints.advanced_bands(FILTERS, 0): [search_page([band_row(146, "Darkthrone")], 2)],
        endpoints.advanced_bands(FILTERS, 1): [search_page([band_row(999, "Gone")], 2)],
        endpoints.band(146): [ok(fx["synthetic_band"])],
        endpoints.discography(146): [ok(fx["synthetic_discography"])],
        endpoints.album(1067): [ok(fx["synthetic_album"])],
        endpoints.album(1066): [ok(fx["synthetic_split_album"])],
        endpoints.artist(1323): [ok(fx["synthetic_artist"])],
        endpoints.artist(1324): [ok(fx["synthetic_artist"])],
        endpoints.artist(1325): [ok(status=500)],
    }


def queue_status(store):
    return dict(store.db.execute("SELECT url, status FROM queue").fetchall())


def test_seed_crawl_parse_export(make_client, store, tmp_path):
    client, transport, _ = make_client(routes())
    assert seed_advanced(store, client, FILTERS) == 2
    assert {r[0] for r in store.db.execute("SELECT band_id FROM band_index")} == {146, 999}

    stats = crawl(store, client, max_requests=3)
    assert stats["fetched"] == 3

    stats = crawl(store, client)
    status = queue_status(store)
    assert status[endpoints.band(146)] == "done"
    assert status[endpoints.band(999)] == "gone"
    assert status[endpoints.album(1067)] == "done"
    assert status[endpoints.artist(1325)] == "error"
    # see-also bands from artist pages are not crawled by default
    assert endpoints.band(2018) not in status

    # Resume is idempotent: nothing left except the failing artist.
    calls = len(transport.calls)
    crawl(store, client, max_attempts=1)
    assert len(transport.calls) == calls

    stats = rebuild(store)
    assert stats["band"] == 1 and stats["album"] == 2 and stats["artist"] == 2
    db = store.db
    assert db.execute("SELECT name, formed_year FROM bands").fetchall() == [("Darkthrone", 1986)]
    assert db.execute("SELECT count(*) FROM tracks").fetchone() == (3,)
    assert db.execute("SELECT count(*) FROM band_members").fetchone() == (3,)
    assert db.execute("SELECT count(*) FROM discography").fetchone() == (2,)
    assert sorted(db.execute("SELECT album_id, band_id FROM album_bands").fetchall()) == [
        (1066, 10),
        (1066, 20),
        (1067, 146),
    ]
    assert db.execute("SELECT location FROM band_index WHERE band_id = 146").fetchone() == ("Oslo",)

    paths = export(store, tmp_path / "out")
    assert len(paths) == 11
    assert pl.read_parquet(tmp_path / "out" / "tracks.parquet").height == 3

    assert "pending" in status_report(store, 3.0)


def test_expand_depth_enqueues_artist_bands(make_client, store):
    client, _, _ = make_client(routes())
    seed_advanced(store, client, FILTERS)
    crawl(store, client, kinds=["band", "artist"], expand_depth=1, max_attempts=1)
    status = queue_status(store)
    assert status[endpoints.band(2018)] == "gone"  # crawled at depth 1 (404 in fake routes)
    depth = store.db.execute("SELECT depth FROM queue WHERE url = ?", (endpoints.band(2018),)).fetchone()
    assert depth == (1,)
    # depth-1 bands don't pull in artists; Darkthrone (seeded) keeps depth 0
    assert store.db.execute("SELECT depth FROM queue WHERE url = ?", (endpoints.band(146),)).fetchone() == (0,)


def test_refresh_requeues_stale(make_client, store):
    from datetime import UTC, datetime, timedelta

    client, transport, _ = make_client(routes())
    seed_advanced(store, client, FILTERS)
    crawl(store, client, kinds=["band"])
    calls = len(transport.calls)
    crawl(store, client, kinds=["band"], refresh_older_than=datetime.now(UTC) + timedelta(seconds=5))
    assert len(transport.calls) == calls + 2
