import gzip
import json

import pytest

from ecom_seo_audit.offline import load_offline
from ecom_seo_audit.sitemap import SitemapError, load_sitemap_urls, parse_sitemap

URLSET = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
 <url><loc>https://a.com/p1</loc><lastmod>2026-01-01</lastmod></url>
 <url><loc> https://a.com/p2 </loc></url>
</urlset>"""
INDEX = """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
 <sitemap><loc>https://a.com/product-sitemap.xml</loc></sitemap>
 <sitemap><loc>https://a.com/post-sitemap.xml</loc></sitemap>
</sitemapindex>"""


def test_parse_urlset():
    pages, children = parse_sitemap(URLSET)
    assert pages == ["https://a.com/p1", "https://a.com/p2"] and children == []


def test_parse_index():
    pages, children = parse_sitemap(INDEX)
    assert pages == [] and len(children) == 2


def test_parse_gzip_bytes():
    pages, _ = parse_sitemap(gzip.compress(URLSET.encode()))
    assert len(pages) == 2


def test_parse_text_list():
    pages, _ = parse_sitemap("https://a.com/1\n# c\nhttps://a.com/2\n")
    assert pages == ["https://a.com/1", "https://a.com/2"]


@pytest.mark.parametrize("bad", ["", "<html></html>", "<urlset><url>", "not a sitemap"])
def test_parse_errors(bad):
    with pytest.raises(SitemapError):
        parse_sitemap(bad)


def fake_fetch(mapping):
    return lambda url: mapping.get(url)


def test_load_follows_index_with_pattern():
    mapping = {
        "https://a.com/index.xml": INDEX.encode(),
        "https://a.com/product-sitemap.xml": URLSET.encode(),
        "https://a.com/post-sitemap.xml": b"<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><url><loc>https://a.com/post</loc></url></urlset>",
    }
    urls = load_sitemap_urls(fake_fetch(mapping), "https://a.com/index.xml", child_pattern="product")
    assert urls == ["https://a.com/p1", "https://a.com/p2"]
    all_urls = load_sitemap_urls(fake_fetch(mapping), "https://a.com/index.xml")
    assert "https://a.com/post" in all_urls


def test_load_respects_max_urls_and_dedupes():
    mapping = {"https://a.com/s.xml": URLSET.encode()}
    assert load_sitemap_urls(fake_fetch(mapping), "https://a.com/s.xml", max_urls=1) == ["https://a.com/p1"]


def test_load_missing_sitemap_raises():
    with pytest.raises(SitemapError):
        load_sitemap_urls(fake_fetch({}), "https://a.com/none.xml")


def test_load_breaks_cycles():
    loop = b"<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><sitemap><loc>https://a.com/i.xml</loc></sitemap></sitemapindex>"
    assert load_sitemap_urls(fake_fetch({"https://a.com/i.xml": loop}), "https://a.com/i.xml") == []


# ------------------------------------------------------------------ offline
def test_offline_reads_url_comment(tmp_path):
    (tmp_path / "a.html").write_text("<!-- url: https://x.com/a -->\n<html></html>")
    assert load_offline(str(tmp_path)) == [("https://x.com/a", "<!-- url: https://x.com/a -->\n<html></html>")]


def test_offline_reads_saved_from_comment(tmp_path):
    (tmp_path / "a.html").write_text("<!-- saved from url=(0020)https://x.com/saved -->\n<html></html>")
    assert load_offline(str(tmp_path))[0][0] == "https://x.com/saved"


def test_offline_manifest_wins(tmp_path):
    (tmp_path / "a.html").write_text("<!-- url: https://x.com/comment -->")
    (tmp_path / "manifest.json").write_text(json.dumps({"a.html": "https://x.com/manifest"}))
    assert load_offline(str(tmp_path))[0][0] == "https://x.com/manifest"


def test_offline_placeholder_url(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.htm").write_text("<html></html>")
    assert load_offline(str(tmp_path))[0][0] == "https://offline.invalid/sub/b.htm"


def test_offline_single_file(tmp_path):
    f = tmp_path / "one.html"
    f.write_text("<!-- url: https://x.com/one -->")
    assert load_offline(str(f))[0][0] == "https://x.com/one"


def test_offline_ignores_non_html(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    assert load_offline(str(tmp_path)) == []


def test_offline_missing_path():
    with pytest.raises(FileNotFoundError):
        load_offline("/nonexistent/path/for/test")
