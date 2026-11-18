import pytest

from conftest import ok
from metallum_scraper.client import BlockedError, FetchError
from metallum_scraper.endpoints import BASE_URL

URL = f"{BASE_URL}/band/view/id/1"
ROOT = f"{BASE_URL}/"


def test_fetch_caches_and_throttles(make_client, store):
    client, transport, clock = make_client({URL: [ok("band")], URL + "2": [ok("other")]})
    assert client.fetch(URL).text == "band"
    assert client.get(URL).text == "band"  # served from cache
    client.fetch(URL + "2")
    assert transport.calls == [URL, URL + "2"]
    assert clock.sleeps == [3.0]
    assert store.get_page(URL).body == b"band"


def test_404_is_cached_not_error(make_client, store):
    client, _, _ = make_client({})
    page = client.fetch(URL)
    assert page.status == 404
    assert store.get_page(URL).status == 404


def test_403_rebuilds_session_once(make_client):
    client, transport, _ = make_client({URL: [ok(status=403), ok("band")], ROOT: [ok("home")]})
    assert client.fetch(URL).text == "band"
    assert transport.resets == 1
    assert transport.calls == [URL, ROOT, URL]


def test_second_403_starts_cooldown(make_client):
    client, transport, _ = make_client({URL: [ok(status=403)], ROOT: [ok("home")]})
    with pytest.raises(BlockedError):
        client.fetch(URL)
    n = len(transport.calls)
    with pytest.raises(BlockedError):
        client.fetch(URL)  # fails fast during cooldown
    assert len(transport.calls) == n
    client.wait_unblocked()
    with pytest.raises(BlockedError):
        client.fetch(URL)
    assert len(transport.calls) > n


def test_challenge_page_with_200_is_treated_as_403(make_client, store):
    challenge = ok("<html><title>Just a moment...</title></html>")
    client, transport, _ = make_client({URL: [challenge, ok("band")], ROOT: [ok()]})
    assert client.fetch(URL).text == "band"
    assert transport.resets == 1


def test_429_honors_retry_after(make_client):
    client, _, clock = make_client({URL: [ok(status=429, headers={"Retry-After": "30"}), ok("band")]})
    with pytest.raises(BlockedError) as e:
        client.fetch(URL)
    assert e.value.until == pytest.approx(clock() + 30)
    client.wait_unblocked()
    assert client.fetch(URL).text == "band"


def test_5xx_retries_with_backoff(make_client):
    client, transport, _ = make_client({URL: [ok(status=503), ok(status=502), ok("band")]})
    assert client.fetch(URL).text == "band"
    assert len(transport.calls) == 3


def test_5xx_gives_up(make_client, store):
    client, transport, _ = make_client({URL: [ok(status=500)]})
    with pytest.raises(FetchError):
        client.fetch(URL)
    assert len(transport.calls) == 1 + client.max_retries
    assert store.get_page(URL) is None


def test_network_error_retries(make_client):
    calls = []

    def route(url):
        calls.append(url)
        if len(calls) == 1:
            raise ConnectionError("boom")
        return ok("band")

    client, _, _ = make_client(route)
    assert client.fetch(URL).text == "band"
