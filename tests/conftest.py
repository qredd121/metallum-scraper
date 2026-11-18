from collections.abc import Callable

import pytest

from metallum_scraper.client import Client, RawResponse
from metallum_scraper.store import Store


class FakeTransport:
    """Serves scripted responses. ``routes`` maps URL -> list of responses (consumed in order, last repeats)."""

    def __init__(self, routes: dict[str, list[RawResponse]] | Callable[[str], RawResponse]):
        self.routes = routes
        self.calls: list[str] = []
        self.resets = 0

    def get(self, url: str) -> RawResponse:
        self.calls.append(url)
        if callable(self.routes):
            return self.routes(url)
        queue = self.routes.get(url)
        if not queue:
            return RawResponse(404, b"", {})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def reset(self) -> None:
        self.resets += 1

    def close(self) -> None:
        pass


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s

    def __call__(self) -> float:
        return self.t


def ok(body: str | bytes = b"ok", status: int = 200, headers: dict | None = None) -> RawResponse:
    return RawResponse(status, body.encode() if isinstance(body, str) else body, headers or {})


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite")
    yield s
    s.close()


@pytest.fixture
def make_client(store):
    def make(routes) -> tuple[Client, FakeTransport, FakeClock]:
        transport = FakeTransport(routes)
        clock = FakeClock()
        client = Client(store, transport, delay=3.0, jitter=0.0, sleep=clock.sleep, clock=clock)
        return client, transport, clock

    return make
