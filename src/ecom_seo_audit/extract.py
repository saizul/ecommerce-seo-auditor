"""Parse a product/category page into a structured Page object."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from .util import find_amounts, parse_number

PRODUCT_TYPES = {"Product", "ProductGroup", "IndividualProduct", "SomeProducts"}
NOISE_KEYS = (
    "related", "upsell", "cross-sell", "crosssell", "recommend", "similar", "recently",
    "mini-cart", "minicart", "site-footer", "site-header", "sidebar", "menu",
)
PRICE_EXCLUDE_WORDS = {
    "regular", "compare", "old", "was", "before", "original", "strike", "save", "discount",
    "badge", "percent", "unit", "saving", "sticker",
}
OOS_CLASSES = {"out-of-stock", "outofstock", "sold-out", "soldout", "stock-out"}
IN_CLASSES = {"in-stock", "instock"}
OOS_TEXT = re.compile(
    r"\b(out of stock|sold out|stock out|currently unavailable|temporarily unavailable)\b", re.I
)
IN_TEXT = re.compile(r"\b(in stock|add to cart|add to basket|buy now|available now)\b", re.I)
REVIEWS_TEXT = [
    re.compile(r"\breviews?\s*\(\s*(\d+)\s*\)", re.I),
    re.compile(r"\b(\d+)\s+(?:customer\s+)?reviews?\b", re.I),
]
DESCRIPTION_SELECTORS = [
    "#tab-description",
    ".woocommerce-product-details__short-description",
    ".product-description",
    ".product__description",
    ".product-single__description",
    ".product_description",
    "[class*=product-description]",
    "[class*=product__description]",
    "[itemprop=description]",
    "[id*=description]",
]


@dataclass
class Markup:
    """One structured-data entity found on the page."""

    source: str  # "jsonld" or "microdata"
    data: Dict[str, Any]


@dataclass
class ImageInfo:
    src: str
    alt: Optional[str]


@dataclass
class Page:
    url: str
    request_url: str = ""
    status: int = 200
    headers: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None
    blocked: bool = False
    page_type: str = "other"
    title: str = ""
    meta_description: str = ""
    canonicals: List[str] = field(default_factory=list)
    canonical_urls: List[str] = field(default_factory=list)
    robots: Set[str] = field(default_factory=set)
    h1s: List[str] = field(default_factory=list)
    body_classes: List[str] = field(default_factory=list)
    og_type: str = ""
    products: List[Markup] = field(default_factory=list)
    breadcrumbs: List[Markup] = field(default_factory=list)
    has_item_list: bool = False
    jsonld_errors: int = 0
    hreflangs: List[Tuple[str, str, str]] = field(default_factory=list)
    images: List[ImageInfo] = field(default_factory=list)
    visible_prices: List[float] = field(default_factory=list)
    meta_price: Optional[float] = None
    stock_signal: Optional[str] = None
    description_text: str = ""
    visible_review_count: Optional[int] = None
    has_pagination: bool = False
    text: str = ""

    @property
    def redirected(self) -> bool:
        return bool(self.request_url) and self.request_url != self.url

    @property
    def noindex(self) -> bool:
        return "noindex" in self.robots or "none" in self.robots

    @property
    def ok(self) -> bool:
        return self.error is None and self.status < 400


# --------------------------------------------------------------------------- helpers
def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def types_of(node: Any) -> List[str]:
    if not isinstance(node, dict):
        return []
    out = []
    for t in as_list(node.get("@type")):
        if isinstance(t, str):
            out.append(t.rsplit("/", 1)[-1].rsplit("#", 1)[-1])
    return out


def _tokens(el: Tag) -> List[str]:
    cls = el.get("class") or []
    if isinstance(cls, str):
        cls = cls.split()
    return [c.lower() for c in cls]


def _is_noise(el: Tag) -> bool:
    if el.name in ("html", "body", "main", "article"):
        return False
    if el.name in ("nav", "footer", "aside"):
        return True
    toks = _tokens(el)
    eid = (el.get("id") or "").lower()
    return any(k in t for t in toks + [eid] for k in NOISE_KEYS)


def _in_noise(el: Tag) -> bool:
    node: Optional[Tag] = el
    while node is not None and getattr(node, "name", None):
        if _is_noise(node):
            return True
        node = node.parent
    return False


def _words(token: str) -> List[str]:
    return [w for w in re.split(r"[-_\s]+", token.lower()) if w]


# --------------------------------------------------------------------------- JSON-LD
def _load_json(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("<!--"):
        raw = raw.replace("<!--", "").replace("-->", "")
    try:
        return json.loads(raw, strict=False)
    except ValueError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
        return json.loads(cleaned, strict=False)


def _collect_ids(data: Any, idmap: Dict[str, Dict[str, Any]]) -> None:
    if isinstance(data, list):
        for x in data:
            _collect_ids(x, idmap)
    elif isinstance(data, dict):
        node_id = data.get("@id")
        if isinstance(node_id, str) and len(data) > 1:
            idmap.setdefault(node_id, data)
        for v in data.values():
            _collect_ids(v, idmap)


def _resolve(value: Any, idmap: Dict[str, Dict[str, Any]], depth: int = 0) -> Any:
    if depth > 6:
        return value
    if isinstance(value, list):
        return [_resolve(v, idmap, depth + 1) for v in value]
    if isinstance(value, dict):
        if set(value) == {"@id"} and value["@id"] in idmap:
            return _resolve(idmap[value["@id"]], idmap, depth + 1)
        return {k: _resolve(v, idmap, depth + 1) for k, v in value.items()}
    return value


def _walk_nodes(data: Any, out: List[Dict[str, Any]]) -> None:
    if isinstance(data, list):
        for x in data:
            _walk_nodes(x, out)
    elif isinstance(data, dict):
        if "@graph" in data:
            _walk_nodes(data["@graph"], out)
            if data.get("@type"):
                out.append(data)
        else:
            out.append(data)
        main = data.get("mainEntity")
        if isinstance(main, (dict, list)):
            _walk_nodes(main, out)


def extract_jsonld(soup: BeautifulSoup) -> Tuple[List[Dict[str, Any]], int]:
    """Return (flattened nodes with @id references resolved, number of unparseable blocks)."""
    nodes: List[Dict[str, Any]] = []
    errors = 0
    idmap: Dict[str, Dict[str, Any]] = {}
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = tag.string if tag.string is not None else tag.get_text()
        if not raw or not raw.strip():
            continue
        try:
            data = _load_json(raw)
        except ValueError:
            errors += 1
            continue
        _collect_ids(data, idmap)
        _walk_nodes(data, nodes)
    return [_resolve(n, idmap) for n in nodes], errors


# --------------------------------------------------------------------------- microdata
def _md_scalar(el: Tag) -> str:
    if el.get("content") is not None:
        return str(el.get("content")).strip()
    if el.name == "meta":
        return str(el.get("content", "")).strip()
    if el.name in ("audio", "embed", "iframe", "img", "source", "track", "video"):
        return str(el.get("src", "")).strip()
    if el.name in ("a", "area", "link"):
        return str(el.get("href", "")).strip()
    if el.name == "object":
        return str(el.get("data", "")).strip()
    if el.name in ("data", "meter"):
        return str(el.get("value", "")).strip()
    if el.name == "time":
        return str(el.get("datetime") or el.get_text(" ", strip=True)).strip()
    return el.get_text(" ", strip=True)


def _md_item(el: Tag) -> Dict[str, Any]:
    itemtype = str(el.get("itemtype", "")).split()
    first = itemtype[0] if itemtype else ""
    item: Dict[str, Any] = {"@type": first.rsplit("/", 1)[-1] if first else ""}
    if el.get("itemid"):
        item["@id"] = el.get("itemid")
    elif el.name in ("a", "link") and el.get("href"):
        item["url"] = el.get("href")

    def visit(parent: Tag) -> None:
        for child in parent.children:
            if not isinstance(child, Tag):
                continue
            names = str(child.get("itemprop", "")).split()
            if names:
                value: Any = _md_item(child) if child.has_attr("itemscope") else _md_scalar(child)
                for name in names:
                    if name in item:
                        existing = item[name]
                        item[name] = (existing if isinstance(existing, list) else [existing]) + [value]
                    else:
                        item[name] = value
            if not child.has_attr("itemscope"):
                visit(child)

    visit(el)
    return item


def extract_microdata(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    items = []
    for el in soup.find_all(attrs={"itemscope": True, "itemtype": True}):
        if el.has_attr("itemprop"):
            continue  # nested entities are reached through their parent
        if "schema.org" not in str(el.get("itemtype")):
            continue
        if _in_noise(el):
            continue
        items.append(_md_item(el))
    return items


# --------------------------------------------------------------------------- DOM scraping
def _text_without(el: Tag, skip_tags: Iterable[str] = ("del", "s", "strike")) -> str:
    skip = set(skip_tags)
    parts = []
    for s in el.find_all(string=True):
        bad = False
        node = s.parent
        while node is not None and node is not el.parent:
            if node.name in skip or any(w in PRICE_EXCLUDE_WORDS for t in _tokens(node) for w in _words(t)):
                if node is not el:
                    bad = True
                    break
            node = node.parent
        if not bad:
            parts.append(str(s))
    return " ".join(parts)


def _visible_prices(soup: BeautifulSoup) -> List[float]:
    seen: List[float] = []
    for el in soup.find_all(True):
        toks = _tokens(el)
        if not any("price" in t for t in toks):
            continue
        if any(w in PRICE_EXCLUDE_WORDS for t in toks for w in _words(t)):
            continue
        if el.find_parent(["del", "s", "strike"]) is not None:
            continue
        for amount in find_amounts(_text_without(el)):
            if amount not in seen:
                seen.append(amount)
    return seen


def _stock_signal(soup: BeautifulSoup, text: str) -> Optional[str]:
    oos_class = in_class = False
    for el in soup.find_all(True):
        toks = set(_tokens(el))
        if toks & OOS_CLASSES:
            oos_class = True
        if toks & IN_CLASSES:
            in_class = True
    oos = oos_class or bool(OOS_TEXT.search(text))
    ins = in_class or bool(IN_TEXT.search(text))
    if oos and not ins:
        return "out"
    if ins and not oos:
        return "in"
    if oos and ins:
        if oos_class and not in_class:
            return "out"
        if in_class and not oos_class:
            return "in"
    return None


def _images(soup: BeautifulSoup) -> List[ImageInfo]:
    out = []
    for img in soup.find_all("img"):
        if img.get("role") in ("presentation", "none") or img.get("aria-hidden") == "true":
            continue
        if str(img.get("width", "")).strip() == "1" or str(img.get("height", "")).strip() == "1":
            continue
        src = (img.get("src") or img.get("data-src") or img.get("data-lazy-src") or "").strip()
        if not src:
            continue
        alt = img.get("alt")
        out.append(ImageInfo(src=src, alt=None if alt is None else str(alt).strip()))
    return out


def _description(soup: BeautifulSoup) -> str:
    best = ""
    for sel in DESCRIPTION_SELECTORS:
        for el in soup.select(sel):
            text = el.get_text(" ", strip=True)
            if len(text) > len(best):
                best = text
    return best


def _prune(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    doomed = [el for el in soup.find_all(True) if _is_noise(el)]
    for el in doomed:
        try:
            el.decompose()
        except AttributeError:  # already removed together with an ancestor
            pass


def _robots_directives(soup: BeautifulSoup, headers: Dict[str, str]) -> Set[str]:
    found: Set[str] = set()
    for meta in soup.find_all("meta", attrs={"name": re.compile(r"^(robots|googlebot)$", re.I)}):
        for part in str(meta.get("content", "")).lower().split(","):
            if part.strip():
                found.add(part.strip())
    for key, value in headers.items():
        if key.lower() == "x-robots-tag":
            for part in str(value).lower().split(","):
                part = part.split(":", 1)[-1] if part.strip().startswith(("googlebot", "bingbot")) else part
                if part.strip():
                    found.add(part.strip())
    return found


def detect_page_type(page: Page) -> str:
    url = page.url.lower()
    path = urlsplit(url).path
    body = set(page.body_classes)
    category_body = {"tax-product_cat", "woocommerce-shop", "post-type-archive-product", "template-collection",
                     "tax-product_tag", "collection"}
    if body & category_body or "/product-category/" in path or (
        "/collections/" in path and "/products/" not in path
    ):
        return "category"
    product_body = {"single-product", "template-product", "product-template-default", "woocommerce-product"}
    if (
        body & product_body
        or page.og_type == "product"
        or "/product/" in path
        or "/products/" in path
        or page.products
    ):
        return "product"
    if page.has_item_list or "/category/" in path or "/shop" in path:
        return "category"
    return "other"


def parse_page(
    url: str,
    html: str,
    status: int = 200,
    headers: Optional[Dict[str, str]] = None,
    request_url: Optional[str] = None,
    page_type: str = "auto",
) -> Page:
    headers = headers or {}
    soup = BeautifulSoup(html or "", "html.parser")
    page = Page(url=url, request_url=request_url or url, status=status, headers=dict(headers))

    page.title = soup.title.get_text(" ", strip=True) if soup.title else ""
    md = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    page.meta_description = str(md.get("content", "")).strip() if md else ""
    for link in soup.find_all("link", rel=True):
        rels = [r.lower() for r in (link["rel"] if isinstance(link["rel"], list) else link["rel"].split())]
        href = str(link.get("href", "")).strip()
        if "canonical" in rels and href:
            page.canonicals.append(href)
            page.canonical_urls.append(urljoin(url, href))
        elif "alternate" in rels and link.get("hreflang") and href:
            page.hreflangs.append((str(link["hreflang"]).strip(), href, urljoin(url, href)))
    page.robots = _robots_directives(soup, headers)
    ogt = soup.find("meta", attrs={"property": "og:type"})
    page.og_type = str(ogt.get("content", "")).strip().lower() if ogt else ""
    body = soup.find("body")
    page.body_classes = _tokens(body) if body else []
    page.h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]

    for prop in ("product:price:amount", "og:price:amount"):
        m = soup.find("meta", attrs={"property": prop})
        if m and parse_number(m.get("content", "")) is not None:
            page.meta_price = parse_number(m.get("content", ""))
            break

    # structured data (extracted before pruning scripts)
    nodes, page.jsonld_errors = extract_jsonld(soup)
    for node in nodes:
        t = set(types_of(node))
        if t & PRODUCT_TYPES:
            page.products.append(Markup("jsonld", node))
        if "BreadcrumbList" in t:
            page.breadcrumbs.append(Markup("jsonld", node))
        if "ItemList" in t or "CollectionPage" in t:
            page.has_item_list = True
    for item in extract_microdata(soup):
        t = item.get("@type", "")
        if t in PRODUCT_TYPES:
            page.products.append(Markup("microdata", item))
        elif t == "BreadcrumbList":
            page.breadcrumbs.append(Markup("microdata", item))
        elif t == "ItemList":
            page.has_item_list = True

    page.has_pagination = bool(
        soup.select("a.page-numbers, .pagination a, nav.pagination, link[rel=next], a[rel=next]")
    )

    _prune(soup)
    page.text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    page.images = _images(soup)
    page.visible_prices = _visible_prices(soup)
    page.stock_signal = _stock_signal(soup, page.text)
    page.description_text = _description(soup)
    for rx in REVIEWS_TEXT:
        m = rx.search(page.text)
        if m:
            page.visible_review_count = int(m.group(1))
            break

    page.page_type = detect_page_type(page) if page_type == "auto" else page_type
    return page
