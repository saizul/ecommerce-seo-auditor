import os

import pytest
import requests

from ecom_seo_audit.fetch import Fetcher
from fakes import Clock, ConnectionError_, FakeResponse, FakeSession

ROBOTS = "User-agent: *\nDisallow: /cart/\nDisallow: /private\n"


def make(routes, **kw):
    clock = Clock()
    session = FakeSession(routes)
    fetcher = Fetcher(session=session, sleep=clock.sleep, clock=clock, delay=kw.pop("delay", 1.0), **kw)
    return fetcher, session, clock


def test_robots_disallow_blocks_fetch():
    f, session, _ = make({"https://a.com/robots.txt": FakeResponse(200, ROBOTS), "https://a.com/cart/x": FakeResponse(200, "x")})
    res = f.fetch("https://a.com/cart/x")
    assert res.blocked and res.error == "blocked by robots.txt"
    assert "https://a.com/cart/x" not in session.calls


def test_robots_allows_other_paths():
    f, _, _ = make({"https://a.com/robots.txt": FakeResponse(200, ROBOTS), "https://a.com/p": FakeResponse(200, "<html>ok</html>")})
    res = f.fetch("https://a.com/p")
    assert not res.blocked and res.status == 200 and "ok" in res.text


def test_robots_fetched_once_per_origin():
    f, session, _ = make({"https://a.com/robots.txt": FakeResponse(200, ROBOTS), "https://a.com/1": FakeResponse(200, "a"), "https://a.com/2": FakeResponse(200, "b")})
    f.fetch("https://a.com/1")
    f.fetch("https://a.com/2")
    assert session.calls.count("https://a.com/robots.txt") == 1


def test_robots_404_means_allow_all():
    f, _, _ = make({"https://a.com/p": FakeResponse(200, "ok")})
    assert not f.fetch("https://a.com/p").blocked


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_robots_unavailable_is_conservative(status):
    f, _, _ = make({"https://a.com/robots.txt": FakeResponse(status, ""), "https://a.com/p": FakeResponse(200, "ok")}, max_retries=0)
    assert f.fetch("https://a.com/p").blocked


def test_robots_network_error_is_conservative():
    f, _, _ = make({"https://a.com/robots.txt": ConnectionError_("down")}, max_retries=0)
    assert f.fetch("https://a.com/p").blocked


def test_no_robots_flag_skips_check_entirely():
    f, session, _ = make({"https://a.com/cart/x": FakeResponse(200, "ok")}, respect_robots=False)
    assert not f.fetch("https://a.com/cart/x").blocked
    assert "https://a.com/robots.txt" not in session.calls


def test_user_agent_specific_rules():
    robots = "User-agent: ecom-seo-audit\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    f, _, _ = make({"https://a.com/robots.txt": FakeResponse(200, robots)})
    assert f.fetch("https://a.com/p").blocked


def test_delay_enforced_between_same_host_requests():
    routes = {"https://a.com/%d" % i: FakeResponse(200, "x") for i in range(3)}
    f, _, clock = make(routes, delay=2.0)
    for i in range(3):
        f.fetch("https://a.com/%d" % i)
    # robots.txt + 3 pages, each separated by >= 2 seconds of (fake) time
    assert clock.now - 1000.0 >= 6.0


def test_delay_not_applied_across_hosts():
    f, _, clock = make({"https://a.com/p": FakeResponse(200, "x"), "https://b.com/p": FakeResponse(200, "y")}, delay=5.0)
    f.fetch("https://a.com/p")
    first = clock.now
    f.fetch("https://b.com/p")
    assert clock.now - first <= 5.0  # only b.com's own robots/page spacing, not a.com's


def test_crawl_delay_from_robots_raises_delay():
    f, _, clock = make({"https://a.com/robots.txt": FakeResponse(200, "User-agent: *\nCrawl-delay: 10\n"), "https://a.com/1": FakeResponse(200, "x"), "https://a.com/2": FakeResponse(200, "y")}, delay=1.0)
    f.fetch("https://a.com/1")
    start = clock.now
    f.fetch("https://a.com/2")
    assert clock.now - start >= 10.0


def test_retry_on_503_then_success():
    routes = {"https://a.com/p": [FakeResponse(503, ""), FakeResponse(200, "<html>ok</html>")]}
    f, session, _ = make(routes)
    assert f.fetch("https://a.com/p").status == 200
    assert session.calls.count("https://a.com/p") == 2


def test_retry_after_header_honoured():
    routes = {"https://a.com/p": [FakeResponse(429, "", headers={"Retry-After": "7"}), FakeResponse(200, "ok")]}
    f, _, clock = make(routes, delay=0)
    f.fetch("https://a.com/p")
    assert 7 in clock.sleeps


def test_gives_up_after_max_retries():
    f, session, _ = make({"https://a.com/p": FakeResponse(503, "")}, max_retries=2)
    res = f.fetch("https://a.com/p")
    assert res.status == 503
    assert session.calls.count("https://a.com/p") == 3


def test_connection_error_reported_not_raised():
    f, _, _ = make({"https://a.com/p": requests.Timeout("slow")}, max_retries=0)
    res = f.fetch("https://a.com/p")
    assert res.status == 0 and "Timeout" in res.error


def test_non_html_rejected():
    f, _, _ = make({"https://a.com/img": FakeResponse(200, "x", headers={"Content-Type": "image/png"})})
    assert "non-HTML" in f.fetch("https://a.com/img").error


def test_oversize_rejected():
    f, _, _ = make({"https://a.com/big": FakeResponse(200, "x" * 100)}, max_bytes=10)
    assert "larger than" in f.fetch("https://a.com/big").error


def test_final_url_follows_redirect():
    f, _, _ = make({"https://a.com/old": FakeResponse(200, "ok", url="https://a.com/new")})
    assert f.fetch("https://a.com/old").final_url == "https://a.com/new"


def test_cache_hit_skips_network(tmp_path):
    routes = {"https://a.com/p": FakeResponse(200, "<html>cached</html>")}
    f, session, _ = make(routes, cache_dir=str(tmp_path))
    f.fetch("https://a.com/p")
    calls = len(session.calls)
    res = f.fetch("https://a.com/p")
    assert res.from_cache and "cached" in res.text and len(session.calls) == calls
    assert os.listdir(str(tmp_path))


def test_cache_expiry(tmp_path):
    routes = {"https://a.com/p": FakeResponse(200, "v1")}
    f, session, _ = make(routes, cache_dir=str(tmp_path), cache_ttl=-1)
    f.fetch("https://a.com/p")
    res = f.fetch("https://a.com/p")
    assert not res.from_cache


def test_server_errors_not_cached(tmp_path):
    f, _, _ = make({"https://a.com/p": FakeResponse(500, "oops")}, cache_dir=str(tmp_path), max_retries=0)
    f.fetch("https://a.com/p")
    assert not [n for n in os.listdir(str(tmp_path)) if n.endswith(".json")]


def test_corrupt_cache_is_ignored(tmp_path):
    routes = {"https://a.com/p": FakeResponse(200, "fresh")}
    f, _, _ = make(routes, cache_dir=str(tmp_path))
    f.fetch("https://a.com/p")
    for name in os.listdir(str(tmp_path)):
        with open(os.path.join(str(tmp_path), name), "w") as fh:
            fh.write("{broken")
    assert f.fetch("https://a.com/p").text == "fresh"


def test_fetch_many_preserves_order_and_limits_workers():
    routes = {"https://a.com/%d" % i: FakeResponse(200, str(i)) for i in range(6)}
    f, _, _ = make(routes, delay=0, concurrency=3)
    urls = ["https://a.com/%d" % i for i in range(6)]
    results = f.fetch_many(urls)
    assert [r.text for r in results] == [str(i) for i in range(6)]


def test_fetch_bytes_for_sitemaps():
    f, _, _ = make({"https://a.com/s.xml": FakeResponse(200, "<urlset/>", headers={"Content-Type": "application/xml"})})
    assert f.fetch_bytes("https://a.com/s.xml") == b"<urlset/>"
    assert f.fetch_bytes("https://a.com/missing.xml") is None
