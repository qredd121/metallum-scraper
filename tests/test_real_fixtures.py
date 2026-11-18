"""Smoke tests against real pages saved with `metallum fixture URL real_<kind>_<name>`.

Real pages are gitignored (no redistribution); these tests skip when none are present.
"""

from pathlib import Path

import pytest

from metallum_scraper.parsers import parse_album, parse_artist, parse_band, parse_discography

FIXTURES = Path(__file__).parent / "fixtures"


def real(kind: str) -> list[Path]:
    return sorted(FIXTURES.glob(f"real_{kind}_*.html"))


def ids(paths):
    return [p.stem for p in paths]


@pytest.mark.parametrize("path", real("band"), ids=ids(real("band")))
def test_real_band(path):
    band = parse_band(path.read_text())
    assert band is not None and band.id and band.name
    assert band.country and band.status and band.genre_raw
    assert band.genres
    assert band.added_on


@pytest.mark.parametrize("path", real("discography"), ids=ids(real("discography")))
def test_real_discography(path):
    releases = parse_discography(path.read_text())
    assert releases
    assert all(r.album_id and r.name and r.type for r in releases)


@pytest.mark.parametrize("path", real("album"), ids=ids(real("album")))
def test_real_album(path):
    album = parse_album(path.read_text())
    assert album is not None and album.id and album.name
    assert album.bands and album.type and album.release_date_raw
    assert album.tracks and all(t.title for t in album.tracks)


@pytest.mark.parametrize("path", real("artist"), ids=ids(real("artist")))
def test_real_artist(path):
    artist = parse_artist(path.read_text())
    assert artist is not None and artist.name
    assert artist.memberships
