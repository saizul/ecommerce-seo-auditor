"""All SEO / structured-data checks. Each check returns a list of Issue objects."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from .extract import Markup, Page, as_list, types_of
from .issues import Issue, make_issue
from .util import (
    first_page_url,
    is_tracking_or_facet,
    non_pagination_params,
    normalise_url,
    page_number,
    parameterised,
    same_site,
    validate_gtin,
)

AVAILABILITY_VALUES = {
    "BackOrder", "Discontinued", "InStock", "InStoreOnly", "LimitedAvailability", "MadeToOrder",
    "OnlineOnly", "OutOfStock", "PreOrder", "PreSale", "SoldOut",
}
IN_AVAILABILITY = {"InStock", "LimitedAvailability", "OnlineOnly", "InStoreOnly"}
OUT_AVAILABILITY = {"OutOfStock", "SoldOut", "Discontinued"}
GTIN_KEYS = ("gtin", "gtin8", "gtin12", "gtin13", "gtin14")
HREFLANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z]{4})?(-([a-z]{2}|\d{3}))?$", re.I)
PLAIN_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


@dataclass
class AuditConfig:
    min_description_words: int = 50
    alt_threshold: float = 0.8
    title_max: int = 60
    price_tolerance: float = 0.01
    today: Optional[date] = None
    page_type: str = "auto"

    def current_date(self) -> date:
        return self.today or date.today()


# --------------------------------------------------------------------------- generic accessors
def _first(value: Any) -> Any:
    items = as_list(value)
    return items[0] if items else None


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@id") or ""
    if isinstance(value, list):
        value = _text(value[0]) if value else ""
    return str(value).strip() if value is not None else ""


def normalise_availability(value: Any) -> str:
    text = _text(value)
    text = text.split("#")[-1].rsplit("/", 1)[-1]
    if text.lower().startswith("schema:"):
        text = text[7:]
    return text


def _variants(product: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [v for v in as_list(product.get("hasVariant")) if isinstance(v, dict)]


def _gather(product: Dict[str, Any], key: str) -> Any:
    value = product.get(key)
    if value not in (None, "", [], {}):
        return value
    for v in _variants(product):
        if v.get(key) not in (None, "", [], {}):
            return v.get(key)
    return None


def leaf_offers(product: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for o in as_list(product.get("offers")):
        if not isinstance(o, dict):
            continue
        subs = [s for s in as_list(o.get("offers")) if isinstance(s, dict)]
        if "AggregateOffer" in types_of(o) and subs:
            for s in subs:
                merged = dict(s)
                merged.setdefault("priceCurrency", o.get("priceCurrency"))
                out.append(merged)
        else:
            out.append(o)
    for v in _variants(product):
        out.extend(leaf_offers(v))
    return out


def offer_price_raw(offer: Dict[str, Any]) -> Any:
    for key in ("price", "lowPrice"):
        if offer.get(key) not in (None, ""):
            return offer[key]
    spec = _first(offer.get("priceSpecification"))
    if isinstance(spec, dict) and spec.get("price") not in (None, ""):
        return spec["price"]
    return None


def offer_currency(offer: Dict[str, Any]) -> str:
    cur = _text(offer.get("priceCurrency"))
    if not cur:
        spec = _first(offer.get("priceSpecification"))
        if isinstance(spec, dict):
            cur = _text(spec.get("priceCurrency"))
    return cur


def _number(value: Any) -> Optional[float]:
    s = str(value).strip()
    if PLAIN_NUMBER_RE.match(s):
        return float(s)
    return None


# --------------------------------------------------------------------------- product schema
def check_product_markup(page: Page, cfg: AuditConfig) -> List[Issue]:
    url = page.url
    out: List[Issue] = []

    def add(code: str, detail: str = "") -> None:
        out.append(make_issue(code, url, detail))

    if page.jsonld_errors:
        add("SCHEMA_JSONLD_INVALID", "%d unparseable JSON-LD block(s)" % page.jsonld_errors)
    if not page.products:
        add("SCHEMA_MISSING")
        return out
    sources = {m.source for m in page.products}
    if len(sources) > 1:
        add("SCHEMA_DUPLICATE_MARKUP")
    primary: Markup = next((m for m in page.products if m.source == "jsonld"), page.products[0])
    p = primary.data

    if not _text(p.get("name")):
        add("PRODUCT_NAME_MISSING")
    if not _gather(p, "image"):
        add("PRODUCT_IMAGE_MISSING")
    if not _text(_gather(p, "description")):
        add("PRODUCT_DESCRIPTION_MISSING")
    if not _text(_gather(p, "brand")):
        add("BRAND_MISSING")

    gtins = {k: _text(_gather(p, k)) for k in GTIN_KEYS if _text(_gather(p, k))}
    if not (_text(_gather(p, "sku")) or gtins or _text(_gather(p, "mpn")) or _text(p.get("productGroupID"))):
        add("IDENTIFIER_MISSING")
    for key, value in gtins.items():
        ok, why = validate_gtin(value)
        if not ok:
            add("GTIN_INVALID", "%s=%s: %s" % (key, value, why))
            break

    out.extend(_check_offers(page, p, cfg))
    out.extend(_check_ratings(page, p))
    return out


def _check_offers(page: Page, p: Dict[str, Any], cfg: AuditConfig) -> List[Issue]:
    url = page.url
    out: List[Issue] = []
    seen = set()

    def add(code: str, detail: str = "") -> None:
        if code not in seen:
            seen.add(code)
            out.append(make_issue(code, url, detail))

    offers = leaf_offers(p)
    if not offers:
        add("OFFER_MISSING")
        return out

    prices: List[float] = []
    availabilities: List[str] = []
    has_policy = False
    for o in offers:
        raw = offer_price_raw(o)
        if raw is None:
            add("OFFER_PRICE_MISSING")
        else:
            num = _number(raw)
            if num is None or num < 0:
                add("PRICE_INVALID", "price=%r" % (raw,))
            else:
                prices.append(num)
                if num == 0:
                    add("PRICE_ZERO")
        high = o.get("highPrice")
        if high is not None and _number(high) is not None:
            prices.append(_number(high))  # type: ignore[arg-type]

        cur = offer_currency(o)
        if not cur:
            add("PRICE_CURRENCY_MISSING")
        elif not re.fullmatch(r"[A-Z]{3}", cur):
            add("PRICE_CURRENCY_INVALID", "priceCurrency=%r" % cur)

        avail = normalise_availability(o.get("availability"))
        if not avail:
            add("AVAILABILITY_MISSING")
        elif avail not in AVAILABILITY_VALUES:
            add("AVAILABILITY_INVALID", "availability=%r" % avail)
        else:
            availabilities.append(avail)

        pvu = _text(o.get("priceValidUntil"))
        if not pvu:
            add("PRICE_VALID_UNTIL_MISSING")
        else:
            m = ISO_DATE_RE.match(pvu)
            parsed = None
            if m:
                try:
                    parsed = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    parsed = None
            if parsed is None:
                add("PRICE_VALID_UNTIL_INVALID", "priceValidUntil=%r" % pvu)
            elif parsed < cfg.current_date():
                add("PRICE_VALID_UNTIL_EXPIRED", "priceValidUntil=%s" % pvu)
        if o.get("shippingDetails") or o.get("hasMerchantReturnPolicy"):
            has_policy = True
    if not has_policy and not (p.get("hasMerchantReturnPolicy") or p.get("shippingDetails")):
        add("SHIPPING_RETURN_MISSING")

    out.extend(_price_consistency(page, prices, cfg))
    out.extend(_stock_consistency(page, availabilities))
    return out


def _price_consistency(page: Page, prices: List[float], cfg: AuditConfig) -> List[Issue]:
    if not prices:
        return []
    candidates = list(page.visible_prices)
    if page.meta_price is not None:
        candidates.append(page.meta_price)
    if not candidates:
        return [make_issue("PRICE_NOT_FOUND_ON_PAGE", page.url)]
    tol = cfg.price_tolerance
    if any(abs(s - v) <= tol for s in prices for v in candidates):
        return []
    detail = "markup %s vs page %s" % (
        ", ".join("%.2f" % x for x in sorted(set(prices))),
        ", ".join("%.2f" % x for x in candidates[:4]),
    )
    return [make_issue("PRICE_MISMATCH", page.url, detail)]


def _stock_consistency(page: Page, availabilities: List[str]) -> List[Issue]:
    if not availabilities or page.stock_signal is None:
        return []
    avail = set(availabilities)
    if page.stock_signal == "out" and avail & IN_AVAILABILITY and not avail & OUT_AVAILABILITY:
        return [make_issue("STOCK_MISMATCH", page.url, "page says out of stock, markup says %s" % sorted(avail)[0])]
    if page.stock_signal == "in" and avail & OUT_AVAILABILITY and not avail & IN_AVAILABILITY:
        return [make_issue("STOCK_MISMATCH", page.url, "page offers purchase, markup says %s" % sorted(avail)[0])]
    return []


def _check_ratings(page: Page, p: Dict[str, Any]) -> List[Issue]:
    url = page.url
    out: List[Issue] = []
    agg = _first(p.get("aggregateRating"))
    reviews = [r for r in as_list(p.get("review")) if isinstance(r, dict)]

    declared = None
    if isinstance(agg, dict):
        value = _number(agg.get("ratingValue")) if agg.get("ratingValue") not in (None, "") else None
        best = _number(agg.get("bestRating", 5)) or 5.0
        worst = _number(agg.get("worstRating", 1))
        worst = 1.0 if worst is None else worst
        if value is None or value < min(worst, 0) or value > best:
            out.append(make_issue("RATING_INVALID", url, "ratingValue=%r (best %s)" % (agg.get("ratingValue"), best)))
        rc = _number(agg.get("ratingCount")) if agg.get("ratingCount") not in (None, "") else None
        rv = _number(agg.get("reviewCount")) if agg.get("reviewCount") not in (None, "") else None
        if rc is None and rv is None:
            out.append(make_issue("RATING_COUNT_MISSING", url))
        else:
            declared = int(rv if rv is not None else rc)  # type: ignore[arg-type]
            if declared <= 0:
                out.append(make_issue("RATING_COUNT_ZERO", url))
        if declared is not None and declared > 0 and len(reviews) > declared:
            out.append(make_issue("REVIEW_COUNT_INCONSISTENT", url,
                                  "%d reviews marked up, count says %d" % (len(reviews), declared)))
        if (
            declared is not None
            and page.visible_review_count is not None
            and page.visible_review_count != declared
        ):
            out.append(make_issue("RATING_VISIBLE_MISMATCH", url,
                                  "markup %d vs page %d" % (declared, page.visible_review_count)))
    bad = 0
    for r in reviews:
        rating = r.get("reviewRating")
        rating_value = rating.get("ratingValue") if isinstance(rating, dict) else None
        if not _text(r.get("author")) or rating_value in (None, ""):
            bad += 1
    if bad:
        out.append(make_issue("REVIEW_INVALID", url, "%d of %d reviews incomplete" % (bad, len(reviews))))
    return out


# --------------------------------------------------------------------------- breadcrumbs
def _crumb_url(li: Dict[str, Any]) -> str:
    item = li.get("item")
    if isinstance(item, dict):
        return _text(item.get("@id") or item.get("url"))
    return _text(item)


def _crumb_name(li: Dict[str, Any]) -> str:
    name = _text(li.get("name"))
    item = li.get("item")
    if not name and isinstance(item, dict):
        name = _text(item.get("name"))
    return name


def check_breadcrumbs(page: Page) -> List[Issue]:
    if not page.breadcrumbs:
        return [make_issue("BREADCRUMB_MISSING", page.url)]
    problems: List[str] = []
    for crumb in page.breadcrumbs:
        elems = [e for e in as_list(crumb.data.get("itemListElement")) if isinstance(e, dict)]
        if len(elems) < 2:
            problems.append("needs at least 2 ListItems (found %d)" % len(elems))
            continue
        for idx, li in enumerate(elems):
            if _text(li.get("position")) == "":
                problems.append("item %d has no position" % (idx + 1))
            if not _crumb_name(li):
                problems.append("item %d has no name" % (idx + 1))
            if idx < len(elems) - 1 and not _crumb_url(li):
                problems.append("item %d has no item URL" % (idx + 1))
        if not problems:
            break  # one valid trail is enough
    if problems:
        return [make_issue("BREADCRUMB_INVALID", page.url, "; ".join(problems[:4]))]
    return []


# --------------------------------------------------------------------------- canonical / indexing
def check_indexing(page: Page) -> List[Issue]:
    if not page.noindex or page.page_type not in ("product", "category"):
        return []
    if parameterised(page.url) or page_number(page.url) > 1:
        return []  # deliberately noindexed facet/pagination variants
    code = "NOINDEX_PRODUCT" if page.page_type == "product" else "NOINDEX_CATEGORY"
    return [make_issue(code, page.url, "robots: %s" % ", ".join(sorted(page.robots)))]


def check_canonical(page: Page) -> List[Issue]:
    url = page.url
    out: List[Issue] = []
    cans = page.canonical_urls
    param = parameterised(url)
    if len({normalise_url(c) for c in cans}) > 1:
        out.append(make_issue("CANONICAL_MULTIPLE", url, "; ".join(cans[:3])))
    if any(not urlsplit(raw).scheme for raw in page.canonicals):
        out.append(make_issue("CANONICAL_RELATIVE", url, page.canonicals[0]))
    if not cans:
        if param:
            out.append(make_issue("PARAM_URL_NO_CANONICAL", url, "params: " + ", ".join(non_pagination_params(url))))
        elif page.page_type in ("product", "category") and not page.noindex:
            out.append(make_issue("CANONICAL_MISSING", url))
        return out
    canon = cans[0]
    if not same_site(canon, url):
        out.append(make_issue("CANONICAL_OFFSITE", url, canon))
        return out
    is_self = normalise_url(canon) == normalise_url(url)
    if param:
        facets = [k for k in non_pagination_params(url) if is_tracking_or_facet(k)]
        if is_self and facets and not page.noindex:
            out.append(make_issue("PARAM_URL_SELF_CANONICAL", url, "params: " + ", ".join(facets)))
    elif not is_self:
        if page_number(url) > 1 and normalise_url(canon) == normalise_url(first_page_url(url)):
            out.append(make_issue("PAGINATION_CANONICAL_TO_FIRST", url, "page %d -> %s" % (page_number(url), canon)))
        else:
            out.append(make_issue("CANONICAL_NOT_SELF", url, canon))
    return out


# --------------------------------------------------------------------------- on-page
def check_onpage(page: Page, cfg: AuditConfig) -> List[Issue]:
    url = page.url
    out: List[Issue] = []
    if not page.title:
        out.append(make_issue("TITLE_MISSING", url))
    elif len(page.title) > cfg.title_max:
        out.append(make_issue("TITLE_LONG", url, "%d characters" % len(page.title)))
    if not page.meta_description:
        out.append(make_issue("META_DESC_MISSING", url))
    if not page.h1s:
        out.append(make_issue("H1_MISSING", url))
    elif len(page.h1s) > 1:
        out.append(make_issue("H1_MULTIPLE", url, "%d H1 elements" % len(page.h1s)))
    return out


def check_thin_description(page: Page, cfg: AuditConfig) -> List[Issue]:
    text = page.description_text
    if not text and page.products:
        text = _text(_gather(page.products[0].data, "description"))
    words = len(re.findall(r"\S+", text or ""))
    if words < cfg.min_description_words:
        return [make_issue("THIN_DESCRIPTION", page.url,
                           "%d words (minimum %d)" % (words, cfg.min_description_words))]
    return []


def check_image_alt(page: Page, cfg: AuditConfig) -> List[Issue]:
    total = len(page.images)
    if not total:
        return []
    with_alt = sum(1 for i in page.images if i.alt)
    coverage = with_alt / total
    if coverage < cfg.alt_threshold:
        return [make_issue("IMAGE_ALT_LOW", page.url,
                           "%d of %d images have alt text (%.0f%%)" % (with_alt, total, coverage * 100))]
    return []


# --------------------------------------------------------------------------- hreflang
def check_hreflang(page: Page) -> List[Issue]:
    if not page.hreflangs:
        return []
    url = page.url
    out: List[Issue] = []
    codes: Dict[str, set] = defaultdict(set)
    for code, _raw, absolute in page.hreflangs:
        codes[code.lower()].add(normalise_url(absolute))
    bad_codes = sorted({c for c, _, _ in page.hreflangs if c.lower() != "x-default" and not HREFLANG_RE.match(c)})
    if bad_codes:
        out.append(make_issue("HREFLANG_INVALID_CODE", url, ", ".join(bad_codes)))
    if any(not urlsplit(raw).scheme for _, raw, _ in page.hreflangs):
        out.append(make_issue("HREFLANG_NOT_ABSOLUTE", url))
    me = {normalise_url(url)} | {normalise_url(c) for c in page.canonical_urls}
    if not any(targets & me for targets in codes.values()):
        out.append(make_issue("HREFLANG_NO_SELF", url))
    dups = sorted(c for c, targets in codes.items() if len(targets) > 1)
    if dups:
        out.append(make_issue("HREFLANG_DUPLICATE", url, ", ".join(dups)))
    if "x-default" not in codes:
        out.append(make_issue("HREFLANG_NO_XDEFAULT", url))
    return out


# --------------------------------------------------------------------------- per-page orchestration
def check_page(page: Page, cfg: AuditConfig) -> List[Issue]:
    if page.error:
        return [make_issue("FETCH_FAILED", page.request_url or page.url, page.error)]
    if page.status >= 400:
        return [make_issue("HTTP_ERROR", page.request_url or page.url, "HTTP %d" % page.status)]
    issues: List[Issue] = []
    if page.redirected:
        issues.append(make_issue("REDIRECT_IN_LIST", page.request_url, "-> %s" % page.url))
    if page.page_type not in ("product", "category"):
        issues.extend(check_indexing(page))
        return issues
    issues.extend(check_indexing(page))
    issues.extend(check_canonical(page))
    issues.extend(check_onpage(page, cfg))
    issues.extend(check_image_alt(page, cfg))
    issues.extend(check_breadcrumbs(page))
    issues.extend(check_hreflang(page))
    if page.page_type == "product":
        issues.extend(check_product_markup(page, cfg))
        issues.extend(check_thin_description(page, cfg))
    return issues


# --------------------------------------------------------------------------- cross-page
def _eligible_for_duplicates(page: Page) -> bool:
    if not page.ok or page.noindex or page.page_type not in ("product", "category"):
        return False
    if page.canonical_urls and normalise_url(page.canonical_urls[0]) != normalise_url(page.url):
        return False
    return True


def check_site(pages: List[Page]) -> Dict[str, List[Issue]]:
    """Duplicate titles/descriptions and hreflang return links across the audited set."""
    result: Dict[str, List[Issue]] = defaultdict(list)
    for attr, code in (("title", "DUP_TITLE"), ("meta_description", "DUP_DESCRIPTION")):
        groups: Dict[str, List[Page]] = defaultdict(list)
        for p in pages:
            value = re.sub(r"\s+", " ", getattr(p, attr)).strip().lower()
            if value and _eligible_for_duplicates(p):
                groups[value].append(p)
        for members in groups.values():
            if len(members) < 2:
                continue
            for p in members:
                others = [m.url for m in members if m is not p]
                detail = "shared with %d other page(s), e.g. %s" % (len(others), others[0])
                result[p.url].append(make_issue(code, p.url, detail))

    by_url = {normalise_url(p.url): p for p in pages if p.ok}
    for p in pages:
        if not p.ok or not p.hreflangs:
            continue
        me = normalise_url(p.url)
        missing = []
        for _code, _raw, absolute in p.hreflangs:
            target = by_url.get(normalise_url(absolute))
            if target is None or target is p:
                continue
            back = {normalise_url(a) for _, _, a in target.hreflangs}
            if me not in back:
                missing.append(target.url)
        if missing:
            result[p.url].append(make_issue("HREFLANG_NO_RETURN", p.url, "no return link from %s" % missing[0]))
    return result
