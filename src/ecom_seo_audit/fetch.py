"""Polite HTTP fetching: robots.txt, per-host delay, bounded concurrency, on-disk cache."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

from . import __version__

DEFAULT_USER_AGENT = "ecom-seo-audit/%s (+https://saizul.com)" % __version__
KEPT_HEADERS = ("content-type", "x-robots-tag", "location", "link")
RETRY_STATUSES = {429, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    status: int = 0
    headers: Dict[str, str] = field(default_factory=dict)
    text: str = ""
    content: bytes = b""
    error: Optional[str] = None
    blocked: bool = False
    from_cache: bool = False


class RobotsRules:
    def __init__(self, parser: Optional[RobotFileParser], disallow_all: bool = False, note: str = ""):
        self.parser = parser
        self.disallow_all = disallow_all
        self.note = note

    def allowed(self, user_agent: str, url: str) -> bool:
        if self.disallow_all:
            return False
        if self.parser is None:
            return True
        return self.parser.can_fetch(user_agent, url)

    def crawl_delay(self, user_agent: str) -> Optional[float]:
        if self.parser is None:
            return None
        value = self.parser.crawl_delay(user_agent)
        return float(value) if value is not None else None


class Fetcher:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        delay: float = 1.0,
        concurrency: int = 4,
        timeout: float = 20.0,
        cache_dir: Optional[str] = None,
        cache_ttl: float = 86400.0,
        respect_robots: bool = True,
        session: Any = None,
        max_retries: int = 2,
        max_bytes: int = 5_000_000,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.user_agent = user_agent
        self.delay = max(0.0, delay)
        self.concurrency = max(1, concurrency)
        self.timeout = timeout
        self.cache_dir = cache_dir
        self.cache_ttl = cache_ttl
        self.respect_robots = respect_robots
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.max_bytes = max_bytes
        self._sleep = sleep
        self._clock = clock
        self._hosts: Dict[str, float] = {}
        self._host_lock = threading.Lock()
        self._robots: Dict[str, RobotsRules] = {}
        self._robots_locks: Dict[str, threading.Lock] = {}
        self._robots_guard = threading.Lock()
        self.request_count = 0

    # ----------------------------------------------------------------- throttling
    def _throttle(self, host: str, delay: float) -> None:
        """Reserve the next request slot for a host and sleep until it arrives."""
        with self._host_lock:
            now = self._clock()
            slot = max(now, self._hosts.get(host, now))
            self._hosts[host] = slot + delay
            wait = slot - now
        if wait > 0:
            self._sleep(wait)

    def _delay_for(self, url: str) -> float:
        delay = self.delay
        if self.respect_robots:
            rules = self.robots_for(url)
            crawl = rules.crawl_delay(self.user_agent)
            if crawl:
                delay = max(delay, crawl)
        return delay

    # ----------------------------------------------------------------- raw GET with retries
    def _get(self, url: str, delay: float) -> Any:
        host = urlsplit(url).netloc.lower()
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._throttle(host, delay)
            self.request_count += 1
            try:
                resp = self.session.get(
                    url,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    },
                    timeout=self.timeout,
                    allow_redirects=True,
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    self._sleep(min(2 ** attempt, 30))
                    continue
                raise
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                retry_after = resp.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.strip().isdigit() else 2 ** attempt
                self._sleep(min(wait, 30))
                continue
            return resp
        raise last_exc or RuntimeError("unreachable")

    # ----------------------------------------------------------------- robots.txt
    def robots_for(self, url: str) -> RobotsRules:
        parts = urlsplit(url)
        origin = "%s://%s" % (parts.scheme, parts.netloc)
        with self._robots_guard:
            lock = self._robots_locks.setdefault(origin, threading.Lock())
        with lock:
            if origin in self._robots:
                return self._robots[origin]
            rules = self._load_robots(origin)
            self._robots[origin] = rules
            return rules

    def _load_robots(self, origin: str) -> RobotsRules:
        robots_url = origin + "/robots.txt"
        try:
            resp = self._get(robots_url, self.delay)
        except requests.RequestException as exc:
            return RobotsRules(None, True, "robots.txt unreachable: %s" % exc)
        status = resp.status_code
        if status == 200:
            parser = RobotFileParser()
            parser.parse(resp.text.splitlines())
            return RobotsRules(parser)
        if status in (401, 403) or status >= 500:
            return RobotsRules(None, True, "robots.txt returned HTTP %d" % status)
        return RobotsRules(None, False, "no robots.txt (HTTP %d)" % status)

    def is_allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        return self.robots_for(url).allowed(self.user_agent, url)

    # ----------------------------------------------------------------- cache
    def _cache_path(self, url: str) -> Optional[str]:
        if not self.cache_dir:
            return None
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, key + ".json")

    def _cache_read(self, url: str) -> Optional[FetchResult]:
        path = self._cache_path(url)
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if time.time() - data.get("fetched_at", 0) > self.cache_ttl:
            return None
        return FetchResult(
            url=url,
            final_url=data.get("final_url", url),
            status=int(data.get("status", 0)),
            headers=data.get("headers", {}),
            text=data.get("text", ""),
            from_cache=True,
        )

    def _cache_write(self, result: FetchResult) -> None:
        path = self._cache_path(result.url)
        if not path:
            return
        os.makedirs(self.cache_dir or ".", exist_ok=True)
        payload = {
            "url": result.url,
            "final_url": result.final_url,
            "status": result.status,
            "headers": result.headers,
            "text": result.text,
            "fetched_at": time.time(),
        }
        tmp = path + ".%d.tmp" % threading.get_ident()
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)

    # ----------------------------------------------------------------- public API
    def fetch(self, url: str, raw: bool = False) -> FetchResult:
        """Fetch one URL. raw=True skips the cache and the HTML content-type check (sitemaps)."""
        if not raw:
            cached = self._cache_read(url)
            if cached is not None:
                return cached
        if self.respect_robots and not self.is_allowed(url):
            return FetchResult(url=url, error="blocked by robots.txt", blocked=True)
        try:
            resp = self._get(url, self._delay_for(url))
        except requests.RequestException as exc:
            return FetchResult(url=url, error="%s: %s" % (type(exc).__name__, exc))
        headers = {
            k.lower(): v for k, v in resp.headers.items() if k.lower() in KEPT_HEADERS
        }
        content = resp.content or b""
        if len(content) > self.max_bytes:
            return FetchResult(url=url, status=resp.status_code, error="response larger than %d bytes" % self.max_bytes)
        result = FetchResult(
            url=url,
            final_url=str(getattr(resp, "url", url) or url),
            status=resp.status_code,
            headers=headers,
            content=content if raw else b"",
        )
        ctype = headers.get("content-type", "").lower()
        if not raw:
            if ctype and "html" not in ctype and "xml" not in ctype:
                result.error = "non-HTML content type: %s" % ctype.split(";")[0]
                return result
            result.text = resp.text
            if resp.status_code < 500:
                self._cache_write(result)
        return result

    def fetch_many(
        self, urls: List[str], progress: Optional[Callable[[FetchResult], None]] = None
    ) -> List[FetchResult]:
        def work(u: str) -> FetchResult:
            res = self.fetch(u)
            if progress:
                progress(res)
            return res

        if self.concurrency == 1:
            return [work(u) for u in urls]
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            return list(pool.map(work, urls))

    def fetch_bytes(self, url: str) -> Optional[bytes]:
        res = self.fetch(url, raw=True)
        if res.error or res.status >= 400:
            return None
        return res.content
