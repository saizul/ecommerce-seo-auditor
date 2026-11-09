"""Sitemap parsing (urlset, sitemap index, gzip, plain-text)."""
from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET
from typing import Callable, List, Optional, Tuple


class SitemapError(Exception):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(content) -> Tuple[List[str], List[str]]:
    """Return (page_urls, child_sitemap_urls) for sitemap XML, gzip bytes or a text list."""
    if isinstance(content, bytes):
        if content[:2] == b"\x1f\x8b":
            try:
                content = gzip.decompress(content)
            except OSError as exc:
                raise SitemapError("invalid gzip sitemap: %s" % exc) from exc
        content = content.decode("utf-8", errors="replace")
    text = content.strip().lstrip("﻿")
    if not text:
        raise SitemapError("empty sitemap")
    if not text.startswith("<"):
        urls = [ln.strip() for ln in text.splitlines() if re.match(r"https?://", ln.strip())]
        if not urls:
            raise SitemapError("not a sitemap")
        return urls, []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SitemapError("invalid sitemap XML: %s" % exc) from exc
    kind = _local(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        raise SitemapError("unexpected root element <%s>" % kind)
    pages: List[str] = []
    children: List[str] = []
    for node in root:
        loc = None
        for sub in node:
            if _local(sub.tag) == "loc" and sub.text:
                loc = sub.text.strip()
        if not loc:
            continue
        if _local(node.tag) == "sitemap":
            children.append(loc)
        elif _local(node.tag) == "url":
            pages.append(loc)
    return pages, children


def load_sitemap_urls(
    fetch_text: Callable[[str], Optional[bytes]],
    sitemap_url: str,
    max_urls: Optional[int] = None,
    child_pattern: Optional[str] = None,
    max_depth: int = 3,
) -> List[str]:
    """Follow sitemap indexes up to max_depth and collect unique page URLs in order."""
    out: List[str] = []
    seen_sitemaps = set()
    rx = re.compile(child_pattern) if child_pattern else None

    def walk(url: str, depth: int) -> None:
        if url in seen_sitemaps or depth > max_depth:
            return
        seen_sitemaps.add(url)
        body = fetch_text(url)
        if body is None:
            raise SitemapError("could not fetch sitemap %s" % url)
        pages, children = parse_sitemap(body)
        for u in pages:
            if u not in out:
                out.append(u)
            if max_urls and len(out) >= max_urls:
                return
        for child in children:
            if max_urls and len(out) >= max_urls:
                return
            if rx and not rx.search(child):
                continue
            walk(child, depth + 1)

    walk(sitemap_url, 0)
    return out[:max_urls] if max_urls else out
