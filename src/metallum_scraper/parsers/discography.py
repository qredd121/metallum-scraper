"""Discography tab: ``/band/discography/id/{id}/tab/all``."""

from dataclasses import dataclass

from ._html import LexborHTMLParser, entity_id, first_link, href, parse_reviews, text, to_int


@dataclass
class Release:
    album_id: int | None
    name: str | None
    type: str | None
    year: int | None
    reviews_raw: str | None
    review_count: int | None
    review_avg: int | None


def parse_discography(html: str) -> list[Release]:
    doc = LexborHTMLParser(html)
    releases = []
    for row in doc.css("tr"):
        cells = row.css("td")
        if len(cells) < 3:
            continue
        link = first_link(cells[0])
        album_id = entity_id(href(link))
        if album_id is None:
            continue
        reviews = text(cells[3]) if len(cells) > 3 else None
        count, avg = parse_reviews(reviews)
        releases.append(
            Release(
                album_id=album_id,
                name=text(link),
                type=text(cells[1]),
                year=to_int(text(cells[2])),
                reviews_raw=reviews,
                review_count=count,
                review_avg=avg,
            )
        )
    return releases
