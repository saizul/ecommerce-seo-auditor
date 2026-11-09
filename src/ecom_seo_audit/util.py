"""Small helpers: number parsing, GTIN validation, URL normalisation."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlsplit, urlunsplit

_NUM_RE = re.compile(r"\d{1,3}(?:[,.\s ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?")


def parse_number(text: str) -> Optional[float]:
    """Parse a localised number such as '1,299.00', '1.299,00', '12,50' or '1 299'."""
    if text is None:
        return None
    s = str(text).strip().replace(" ", " ")
    s = re.sub(r"[^\d.,\s-]", "", s).strip()
    if not s or not re.search(r"\d", s):
        return None
    neg = s.startswith("-")
    s = s.lstrip("-").strip()
    s = re.sub(r"\s+", "", s)
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 2 or (len(parts[-1]) == 3 and len(parts[0]) <= 3):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if neg else value


def find_amounts(text: str) -> List[float]:
    """Return every price-like number found in a text snippet."""
    out: List[float] = []
    for m in _NUM_RE.finditer(text or ""):
        value = parse_number(m.group(0))
        if value is not None:
            out.append(value)
    return out


def validate_gtin(value: str) -> Tuple[bool, str]:
    """Validate a GTIN-8/12/13/14 by length and GS1 check digit."""
    digits = str(value).strip()
    if not digits.isdigit():
        return False, "GTIN must contain digits only"
    if len(digits) not in (8, 12, 13, 14):
        return False, "GTIN must be 8, 12, 13 or 14 digits (got %d)" % len(digits)
    body, check = digits[:-1], int(digits[-1])
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    expected = (10 - total % 10) % 10
    if expected != check:
        return False, "bad check digit (expected %d)" % expected
    return True, ""


PAGINATION_PARAMS = {"page", "paged", "p", "pagenumber"}
TRACKING_PARAMS = {
    "gclid", "fbclid", "msclkid", "ref", "mc_cid", "mc_eid", "yclid", "gbraid", "wbraid",
    "_ga", "_gl", "srsltid",
}
FACET_PARAMS = {
    "orderby", "order", "sort", "sort_by", "sortby", "min_price", "max_price", "view", "per_page",
    "limit", "display", "q", "s", "color", "size", "brand", "price", "variant", "add-to-cart",
    "layout", "product_view", "filter", "filters", "post_type", "rating_filter", "instock",
}
FACET_PREFIXES = ("utm_", "filter_", "filter.", "pa_", "attribute_", "query_type_", "pf_", "_pos")


def query_params(url: str) -> Dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))


def is_tracking_or_facet(name: str) -> bool:
    n = name.lower()
    return n in TRACKING_PARAMS or n in FACET_PARAMS or n.startswith(FACET_PREFIXES)


def non_pagination_params(url: str) -> List[str]:
    return [k for k in query_params(url) if k.lower() not in PAGINATION_PARAMS]


def parameterised(url: str) -> bool:
    return bool(non_pagination_params(url))


def normalise_url(url: str) -> str:
    """Normalise for comparison: lowercase host, drop fragment, default port and trailing slash."""
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower()
    if parts.port and not (
        (parts.scheme == "http" and parts.port == 80) or (parts.scheme == "https" and parts.port == 443)
    ):
        host = "%s:%d" % (host, parts.port)
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))


def strip_www(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def same_site(a: str, b: str) -> bool:
    return strip_www(urlsplit(a).hostname or "") == strip_www(urlsplit(b).hostname or "")


_PAGE_PATH_RE = re.compile(r"/page/(\d+)/?$", re.I)


def page_number(url: str) -> int:
    """Return the pagination index of a URL (1 when unpaginated)."""
    parts = urlsplit(url)
    for key, value in parse_qsl(parts.query):
        if key.lower() in PAGINATION_PARAMS and value.isdigit():
            return max(1, int(value))
    m = _PAGE_PATH_RE.search(parts.path)
    return max(1, int(m.group(1))) if m else 1


def first_page_url(url: str) -> str:
    """Strip pagination from a URL so it can be compared with the first page."""
    parts = urlsplit(url)
    query = "&".join(
        "%s=%s" % (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in PAGINATION_PARAMS
    )
    path = _PAGE_PATH_RE.sub("/", parts.path)
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))
