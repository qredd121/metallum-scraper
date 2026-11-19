import html
import json
from pathlib import Path

import pytest

from metallum_scraper import endpoints
from metallum_scraper.parsers import (
    advanced_layout,
    parse_album,
    parse_artist,
    parse_band,
    parse_band_rows,
    parse_discography,
    parse_listing,
)
from metallum_scraper.parsers._html import parse_duration, parse_release_date, parse_reviews, years_in_parens
from metallum_scraper.parsers.band import tokenize_genres
from metallum_scraper.parsers.listing import LETTER_LAYOUT, ListingError

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_band():
    band = parse_band(fixture("synthetic_band.html"), endpoints.band(146))
    assert band.id == 146
    assert band.name == "Darkthrone"
    assert (band.country, band.country_code, band.location) == ("Norway", "NO", "Kolbotn, Viken")
    assert band.status == "Active"
    assert (band.formed_raw, band.formed_year) == ("1986", 1986)
    assert band.years_active_raw == "1986-1987 (as Black Death), 1987-present"
    assert band.themes_raw == "Satanism, Darkness, Metal"
    assert (band.label_id, band.label_name, band.label_kind) == (14, "Peaceville Records", "current")
    assert (band.added_on, band.modified_on) == ("2002-07-21 13:26:36", "2025-03-04 10:11:12")
    assert [(g.token, g.period) for g in band.genres] == [
        ("Death Metal", "early"),
        ("Black Metal", "mid"),
        ("Crust Punk", "mid"),
        ("Heavy/Doom Metal", "later"),
    ]
    assert [(m.artist_id, m.lineup_type) for m in band.members] == [
        (1323, "current"),
        (1324, "current"),
        (1325, "past"),
    ]
    fenriz = band.members[0]
    assert fenriz.roles_raw == "Drums (1986-present), Vocals (1987-present)"
    assert fenriz.years_raw == "1986-present; 1987-present"
    assert [(s.band_id, s.name) for s in fenriz.see_also] == [(2018, "Isengard"), (2019, "Neptune Towers")]
    assert band.members[1].see_also == []


def test_band_missing_page():
    assert parse_band("<html><body>Not found</body></html>") is None


def test_tokenize_genres_nested_parens():
    tokens = tokenize_genres("Black Metal (early, 1990-1993); Ambient (later)")
    assert [(t.token, t.period) for t in tokens] == [("Black Metal", "early, 1990-1993"), ("Ambient", "later")]


def test_discography():
    releases = parse_discography(fixture("synthetic_discography.html"))
    assert [(r.album_id, r.name, r.type, r.year) for r in releases] == [
        (1066, "Soulside Journey", "Full-length", 1991),
        (1067, "Transilvanian Hunger", "Full-length", 1994),
    ]
    assert (releases[0].review_count, releases[0].review_avg) == (12, 82)
    assert releases[1].reviews_raw is None


def test_album():
    album = parse_album(fixture("synthetic_album.html"), endpoints.album(1067))
    assert (album.id, album.name, album.type) == (1067, "Transilvanian Hunger", "Full-length")
    assert [(b.band_id, b.name) for b in album.bands] == [(146, "Darkthrone")]
    assert (album.release_date_raw, album.release_date) == ("February 17th, 1994", "1994-02-17")
    assert (album.label_id, album.label_name, album.catalog_id, album.format) == (
        14,
        "Peaceville Records",
        "VILE 43",
        "CD",
    )
    assert (album.review_count, album.review_avg) == (23, 85)
    assert [
        (t.position, t.song_id, t.disc, t.side, t.number, t.duration_s, t.has_lyrics, t.lyrics_note)
        for t in album.tracks
    ] == [
        (1, 22406, 1, "Side A", 1, 369, True, None),
        (2, 22407, 1, "Side A", 2, 149, False, "instrumental"),
        (3, 22408, 2, None, 1, 3723, False, None),
    ]
    assert [(e.band_id, e.artist_id, e.artist_name, e.section, e.role_raw) for e in album.lineup] == [
        (146, 1323, "Fenriz", "band", "Drums, Vocals, Guitars, Bass"),
        (146, 1555, "Skoll", "guest", "Drums"),
        (146, None, "Uncredited Person", "misc", "Cover art"),
    ]


def test_album_repeated_song_on_both_sides():
    album = parse_album(fixture("synthetic_side_album.html"), endpoints.album(209001))
    assert [(t.position, t.song_id, t.anchor_raw, t.side, t.number, t.has_lyrics) for t in album.tracks] == [
        (1, 1479887, "1479887A", "Side A", 1, False),
        (2, 1479889, "1479889A", "Side A", 2, True),
        (3, 1479887, "1479887B", "Side B", 1, False),
        (4, 1479889, "1479889B", "Side B", 2, True),
    ]


def test_split_album():
    album = parse_album(fixture("synthetic_split_album.html"), endpoints.album(555))
    assert [b.band_id for b in album.bands] == [10, 20]
    assert (album.release_date, album.catalog_id, album.label_id, album.label_name) == (
        "2001-03",
        None,
        None,
        "Independent",
    )
    assert (album.review_count, album.review_avg) == (None, None)
    assert [(e.band_id, e.artist_id) for e in album.lineup] == [(10, 1), (20, 2)]


def test_artist():
    artist = parse_artist(fixture("synthetic_artist.html"), endpoints.artist(1323))
    assert (artist.id, artist.name, artist.real_name, artist.gender) == (1323, "Fenriz", "Gylve Fenris Nagell", "Male")
    assert (artist.age_raw, artist.birth_date_raw) == ("54 (born Nov 28th, 1971)", "Nov 28th, 1971")
    assert (artist.country, artist.country_code, artist.place_of_birth) == ("Norway", "NO", "Norway (Kolbotn, Akershus)")
    assert [(m.band_id, m.band_name, m.membership_type, m.years_raw) for m in artist.memberships] == [
        (146, "Darkthrone", "active", "1986-present"),
        (2018, "Isengard", "past", "1989-1995"),
        (None, "Some Pop Band", "past", None),
        (2018, "Isengard", "guest", None),
    ]
    guest = artist.memberships[-1]
    assert guest.roles_raw == "As Fenriz:"
    assert [(a.album_id, a.album_name, a.year, a.role_raw) for a in guest.albums] == [
        (980198, "Vinterskugge", 1993, "Vocals")
    ]


LETTER_JSON = {
    "error": "",
    "iTotalRecords": 2,
    "iTotalDisplayRecords": 2,
    "sEcho": 1,
    "aaData": [
        ["<a href='https://www.metal-archives.com/bands/Darkthrone/146'>Darkthrone</a>", "Norway", "Black Metal",
         '<span class="active">Active</span>'],
        ["<a href='https://www.metal-archives.com/bands/Dark_Funeral/388'>Dark Funeral</a>", "Sweden",
         "Black Metal", '<span class="active">Active</span>'],
    ],
}


@pytest.mark.parametrize(
    "wrap",
    [
        lambda s: s,
        lambda s: f"<pre>{s}</pre>",
        lambda s: f"<html><body>{s}</body></html>",
        lambda s: f"<html><body><pre>{html.escape(s)}</pre></body></html>",
    ],
)
def test_listing_unwrap(wrap):
    listing = parse_listing(wrap(json.dumps(LETTER_JSON)))
    assert listing.total == 2
    bands = parse_band_rows(listing.rows, LETTER_LAYOUT)
    assert [(b.band_id, b.name, b.country, b.genre_raw, b.status) for b in bands] == [
        (146, "Darkthrone", "Norway", "Black Metal", "Active"),
        (388, "Dark Funeral", "Sweden", "Black Metal", "Active"),
    ]


def test_listing_not_json():
    with pytest.raises(ListingError):
        parse_listing("<html><body>Just a moment...</body></html>")


def test_advanced_layout():
    assert advanced_layout({"genre": "black"}) == ["name", "genre_raw", "country"]
    assert advanced_layout({"country": "NO"}) == ["name", "genre_raw", "location"]
    assert advanced_layout({"country": "NO", "location": "Oslo", "themes": "x", "bandLabelName": "y"}) == [
        "name",
        "genre_raw",
        "location",
        "themes_raw",
        "label_name",
    ]
    assert advanced_layout({"location": "Oslo", "bandLabelName": "y"}) == [
        "name",
        "genre_raw",
        "country",
        "location",
        "label_name",
    ]


def test_advanced_rows_country_filtered():
    rows = [['<a href="https://www.metal-archives.com/bands/Mayhem/67">Mayhem</a> <!-- 7.1 -->', "Black Metal",
             "Oslo", "Satanism"]]
    [band] = parse_band_rows(rows, advanced_layout({"country": "NO", "themes": "satan"}))
    assert (band.band_id, band.name, band.location, band.country, band.themes_raw) == (
        67,
        "Mayhem",
        "Oslo",
        None,
        "Satanism",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("February 17th, 1994", "1994-02-17"),
        ("May 1st, 2001", "2001-05-01"),
        ("March 2001", "2001-03"),
        ("1994", "1994"),
        ("Unknown", None),
        ("February 30th, 1994", None),
    ],
)
def test_release_date(raw, expected):
    assert parse_release_date(raw) == expected


def test_small_helpers():
    assert parse_duration("06:09") == 369
    assert parse_duration("") is None
    assert parse_reviews("12 (82%)") == (12, 82)
    assert parse_reviews("1 review (avg. 100%)") == (1, 100)
    assert parse_reviews("None yet") == (None, None)
    assert years_in_parens("Guitars (lead), Bass (1990-1992, 1995)") == "1990-1992, 1995"


def test_endpoints():
    assert endpoints.entity_id("https://www.metal-archives.com/bands/Darkthrone/146") == 146
    assert endpoints.entity_id(endpoints.discography(146)) == 146
    assert endpoints.classify(endpoints.discography(146)) == "discography"
    assert endpoints.classify(endpoints.band(146)) == "band"
    assert endpoints.classify(endpoints.album(1)) == "album"
    assert endpoints.classify(endpoints.artist(1)) == "artist"
    assert endpoints.classify(endpoints.letter("A", 0)) == "letter"
    url = endpoints.advanced_bands({"genre": "black metal", "country": "NO"}, 200)
    assert endpoints.classify(url) == "search"
    assert endpoints.advanced_filters(url) == {"genre": "black metal", "country": "NO"}
    assert "iDisplayStart=200" in url
