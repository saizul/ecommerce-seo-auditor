"""Fake HTTP session used to test the fetcher without network access."""
import requests


class FakeResponse:
    def __init__(self, status=200, text="", headers=None, url=None, content=None):
        self.status_code = status
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self.headers.update(headers or {})
        self.url = url


class FakeSession:
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.calls.append(url)
        route = self.routes.get(url)
        if route is None:
            return FakeResponse(404, "not found", url=url)
        if isinstance(route, list):
            route = route.pop(0) if len(route) > 1 else route[0]
        if isinstance(route, Exception):
            raise route
        if route.url is None:
            route.url = url
        return route


class Clock:
    """Deterministic clock whose sleep() advances time."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


ConnectionError_ = requests.ConnectionError
