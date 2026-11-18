"""Rate-limited fetch client with Cloudflare handling and raw page caching."""

import contextlib
import random
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from loguru import logger

from . import endpoints
from .store import Page, Store

COOLDOWN = 120.0
TIMEOUT = 30.0


class FetchError(Exception):
    """Request failed after retries (network, 5xx, unexpected status). Retry later."""


class BlockedError(FetchError):
    """The site is refusing requests (403 after session rebuild, or 429)."""

    def __init__(self, message: str, until: float):
        super().__init__(message)
        self.until = until


@dataclass(frozen=True)
class RawResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class Transport(Protocol):
    """Swappable HTTP backend (curl_cffi now, Playwright later if needed)."""

    def get(self, url: str) -> RawResponse: ...

    def reset(self) -> None:
        """Drop the session and start a fresh one."""

    def close(self) -> None: ...


def user_agent(contact: str) -> str:
    from curl_cffi.requests.impersonate import DEFAULT_CHROME

    version = re.sub(r"\D", "", DEFAULT_CHROME) or "131"
    # Keep the impersonated Chrome UA (it must match the TLS fingerprint) and append contact info.
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version}.0.0.0 Safari/537.36 metallum-scraper/0.1 (+mailto:{contact})"
    )


class CurlTransport:
    def __init__(self, contact: str, timeout: float = TIMEOUT):
        self.headers = {"User-Agent": user_agent(contact), "From": contact}
        self.timeout = timeout
        self._session = self._new_session()

    def _new_session(self):
        from curl_cffi import requests

        return requests.Session(impersonate="chrome", headers=self.headers)

    def get(self, url: str) -> RawResponse:
        r = self._session.get(url, timeout=self.timeout)
        return RawResponse(r.status_code, r.content, dict(r.headers))

    def reset(self) -> None:
        with contextlib.suppress(Exception):
            self._session.close()
        self._session = self._new_session()

    def close(self) -> None:
        self._session.close()


# Not "challenge-platform": normal pages embed Cloudflare's /cdn-cgi/challenge-platform/ beacon script.
_CHALLENGE_MARKERS = (b"<title>Just a moment...</title>", b"cf_chl_opt")


def looks_like_challenge(body: bytes) -> bool:
    head = body[:20000]
    return any(m in head for m in _CHALLENGE_MARKERS)


class Client:
    """Serialized, polite fetcher. Successful (200) and 404 responses go to the page cache."""

    def __init__(
        self,
        store: Store,
        transport: Transport,
        delay: float = 3.0,
        jitter: float = 1.5,
        max_retries: int = 3,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.store = store
        self.transport = transport
        self.delay = delay
        self.jitter = jitter
        self.max_retries = max_retries
        self.requests_made = 0
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._blocked_until = 0.0

    def cached(self, url: str, newer_than: datetime | None = None) -> Page | None:
        page = self.store.get_page(url)
        if page is None:
            return None
        if newer_than is not None and page.fetched_at < newer_than.isoformat(timespec="seconds"):
            return None
        return page

    def get(self, url: str, newer_than: datetime | None = None) -> Page:
        """Cached page if present (and fresh enough), else fetch."""
        return self.cached(url, newer_than) or self.fetch(url)

    def wait_unblocked(self) -> None:
        remaining = self._blocked_until - self._clock()
        if remaining > 0:
            logger.info(f"waiting {remaining:.0f}s for cooldown")
            self._sleep(remaining)

    def fetch(self, url: str) -> Page:
        remaining = self._blocked_until - self._clock()
        if remaining > 0:
            raise BlockedError(f"cooling down, {remaining:.0f}s left", self._blocked_until)

        rebuilt = False
        attempt = 0
        while True:
            resp, error = self._request(url)
            if resp is not None and resp.status == 200 and looks_like_challenge(resp.body):
                resp = RawResponse(403, resp.body, resp.headers)

            if resp is not None and resp.status in (200, 404):
                return self.store.put_page(url, resp.status, resp.body if resp.status == 200 else b"")

            if resp is not None and resp.status == 403:
                if not rebuilt:
                    rebuilt = True
                    self._rebuild_session()
                    continue
                self._blocked_until = self._clock() + COOLDOWN
                logger.error(f"403 after session rebuild, cooling down {COOLDOWN:.0f}s: {url}")
                raise BlockedError("403 after session rebuild", self._blocked_until)

            if resp is not None and resp.status == 429:
                wait = _retry_after(resp.headers) or COOLDOWN
                self._blocked_until = self._clock() + wait
                logger.error(f"429, cooling down {wait:.0f}s: {url}")
                raise BlockedError("429 rate limited", self._blocked_until)

            retryable = resp is None or resp.status >= 500
            reason = error if resp is None else f"HTTP {resp.status}"
            if not retryable:
                raise FetchError(reason)
            if attempt >= self.max_retries:
                raise FetchError(f"{reason} (after {attempt} retries)")
            backoff = 5 * 2**attempt + random.uniform(0, 2)
            attempt += 1
            logger.warning(f"{reason} for {url}, retry {attempt}/{self.max_retries} in {backoff:.0f}s")
            self._sleep(backoff)

    def _request(self, url: str) -> tuple[RawResponse | None, str | None]:
        self._throttle()
        try:
            resp = self.transport.get(url)
        except Exception as e:
            self.store.log_request(url, None, str(e)[:500])
            return None, f"network error: {e}"
        finally:
            self.requests_made += 1
            self._last_request = self._clock()
        self.store.log_request(url, resp.status)
        logger.debug(f"{resp.status} {url}")
        return resp, None

    def _throttle(self) -> None:
        if self._last_request is None:
            return
        wait = self.delay + random.uniform(0, self.jitter) - (self._clock() - self._last_request)
        if wait > 0:
            self._sleep(wait)

    def _rebuild_session(self) -> None:
        backoff = random.uniform(10, 60)
        logger.warning(f"403, rebuilding session in {backoff:.0f}s")
        self._sleep(backoff)
        self.transport.reset()
        resp, error = self._request(endpoints.BASE_URL + "/")
        if resp is None or resp.status != 200:
            logger.warning(f"warm-up failed: {error or resp.status}")

    def close(self) -> None:
        self.transport.close()


def _retry_after(headers: Mapping[str, str]) -> float | None:
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(seconds, 1.0), COOLDOWN) if seconds > 0 else None
