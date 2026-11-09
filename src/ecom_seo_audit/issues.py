"""Issue catalog: every check code with its severity, category and recommended fix."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

SEVERITY_WEIGHTS = {"critical": 25, "high": 12, "medium": 6, "low": 2, "info": 0}
SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


@dataclass(frozen=True)
class IssueDef:
    code: str
    severity: str
    category: str
    title: str
    fix: str


@dataclass
class Issue:
    code: str
    severity: str
    category: str
    title: str
    fix: str
    url: str
    detail: str = ""

    @property
    def weight(self) -> int:
        return SEVERITY_WEIGHTS[self.severity]

    def as_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "detail": self.detail,
            "fix": self.fix,
            "url": self.url,
        }


def _d(code, severity, category, title, fix):
    return IssueDef(code, severity, category, title, fix)


_DEFS = [
    # Fetching / indexing
    _d("HTTP_ERROR", "critical", "Indexing", "Page returned an error status",
       "Fix or remove the URL from the sitemap; redirect retired products to the closest category."),
    _d("FETCH_FAILED", "high", "Indexing", "Page could not be fetched",
       "Check server availability, timeouts and firewall rules for the crawler."),
    _d("REDIRECT_IN_LIST", "low", "Indexing", "Listed URL redirects",
       "List the final destination URL in the sitemap instead of a redirecting URL."),
    _d("NOINDEX_PRODUCT", "critical", "Indexing", "Product page is set to noindex",
       "Remove the noindex directive (meta robots or X-Robots-Tag) from sellable product pages."),
    _d("NOINDEX_CATEGORY", "high", "Indexing", "Category page is set to noindex",
       "Remove noindex from category pages you want to rank; keep it only on filter combinations."),
    # Product structured data
    _d("SCHEMA_MISSING", "high", "Structured data", "No Product structured data",
       "Add Product JSON-LD with name, image and offers (price, priceCurrency, availability)."),
    _d("SCHEMA_JSONLD_INVALID", "high", "Structured data", "JSON-LD block is not valid JSON",
       "Fix the syntax error in the JSON-LD script (trailing commas and unescaped quotes are common)."),
    _d("SCHEMA_DUPLICATE_MARKUP", "low", "Structured data",
       "Product marked up twice (JSON-LD and microdata)",
       "Keep one format, preferably JSON-LD, so the two copies cannot drift apart."),
    _d("PRODUCT_NAME_MISSING", "critical", "Structured data", "Product name missing",
       "Add a name property to the Product markup."),
    _d("PRODUCT_IMAGE_MISSING", "high", "Structured data", "Product image missing",
       "Add at least one crawlable, high-resolution image URL to the Product markup."),
    _d("PRODUCT_DESCRIPTION_MISSING", "low", "Structured data", "Product description missing in markup",
       "Add a description property to the Product markup."),
    _d("BRAND_MISSING", "medium", "Structured data", "Brand missing",
       "Add brand (a Brand object with name) to the Product markup."),
    _d("IDENTIFIER_MISSING", "medium", "Structured data", "No sku, gtin or mpn",
       "Add sku and, where available, gtin (8/12/13/14) or mpn to the Product markup."),
    _d("GTIN_INVALID", "high", "Structured data", "GTIN fails length or check-digit validation",
       "Correct the GTIN; it must be 8, 12, 13 or 14 digits with a valid check digit."),
    _d("OFFER_MISSING", "critical", "Structured data", "Product has no offers",
       "Add an offers object (Offer or AggregateOffer) so the product is eligible for price display."),
    _d("OFFER_PRICE_MISSING", "critical", "Structured data", "Offer price missing",
       "Add offers.price (a plain number such as 1299.00) to the Offer."),
    _d("PRICE_INVALID", "high", "Structured data", "Offer price is not a valid number",
       "Use a plain decimal number without currency symbols or thousands separators."),
    _d("PRICE_ZERO", "low", "Structured data", "Offer price is zero",
       "Confirm the product is genuinely free; otherwise fix the price output."),
    _d("PRICE_CURRENCY_MISSING", "high", "Structured data", "priceCurrency missing",
       "Add offers.priceCurrency as an ISO 4217 code such as BDT or USD."),
    _d("PRICE_CURRENCY_INVALID", "medium", "Structured data", "priceCurrency is not an ISO 4217 code",
       "Use a three-letter uppercase ISO 4217 code."),
    _d("AVAILABILITY_MISSING", "medium", "Structured data", "Offer availability missing",
       "Add offers.availability using a schema.org ItemAvailability value such as InStock."),
    _d("AVAILABILITY_INVALID", "medium", "Structured data", "Unrecognised availability value",
       "Use a schema.org ItemAvailability URL, for example https://schema.org/InStock."),
    _d("PRICE_VALID_UNTIL_MISSING", "low", "Structured data", "priceValidUntil missing",
       "Add offers.priceValidUntil (ISO 8601 date) so price snippets stay eligible."),
    _d("PRICE_VALID_UNTIL_INVALID", "medium", "Structured data", "priceValidUntil is not a valid date",
       "Use an ISO 8601 date such as 2027-12-31."),
    _d("PRICE_VALID_UNTIL_EXPIRED", "medium", "Structured data", "priceValidUntil is in the past",
       "Update priceValidUntil to a future date or generate it dynamically."),
    _d("SHIPPING_RETURN_MISSING", "low", "Structured data", "No shipping or return policy markup",
       "Add shippingDetails and hasMerchantReturnPolicy to the Offer for merchant listing features."),
    _d("RATING_INVALID", "high", "Structured data", "aggregateRating has an invalid ratingValue",
       "Provide a numeric ratingValue within the worstRating..bestRating range."),
    _d("RATING_COUNT_MISSING", "high", "Structured data", "aggregateRating has no ratingCount or reviewCount",
       "Add ratingCount or reviewCount to aggregateRating."),
    _d("RATING_COUNT_ZERO", "medium", "Structured data", "aggregateRating count is zero",
       "Remove aggregateRating until the product has reviews."),
    _d("REVIEW_INVALID", "medium", "Structured data", "Review is missing author or rating",
       "Each Review needs an author and reviewRating.ratingValue."),
    _d("REVIEW_COUNT_INCONSISTENT", "medium", "Structured data",
       "Review markup count exceeds the aggregateRating count",
       "Make reviewCount/ratingCount at least as large as the number of marked-up reviews."),
    _d("RATING_VISIBLE_MISMATCH", "medium", "Structured data",
       "Review count in markup differs from the count shown on the page",
       "Markup must reflect what users see; align the numbers."),
    # Price and stock consistency
    _d("PRICE_MISMATCH", "high", "Consistency", "Visible price differs from structured-data price",
       "Make sure the structured-data price is the same price a shopper sees (watch for cached or tax-inclusive values)."),
    _d("PRICE_NOT_FOUND_ON_PAGE", "low", "Consistency", "No visible price could be located",
       "Render the price in server HTML so crawlers can compare it with the markup."),
    _d("STOCK_MISMATCH", "high", "Consistency", "Availability markup disagrees with page text",
       "Drive the availability markup and the on-page stock message from the same stock status."),
    # Canonicals / URLs
    _d("CANONICAL_MISSING", "medium", "Canonical", "No canonical URL",
       "Add a self-referencing rel=canonical to every indexable page."),
    _d("CANONICAL_MULTIPLE", "high", "Canonical", "Multiple conflicting canonical tags",
       "Output a single canonical; disable duplicate output from the theme or SEO plugin."),
    _d("CANONICAL_OFFSITE", "high", "Canonical", "Canonical points to a different domain",
       "Point the canonical at the same host unless you deliberately syndicate."),
    _d("CANONICAL_RELATIVE", "low", "Canonical", "Canonical is a relative URL",
       "Use an absolute canonical URL."),
    _d("CANONICAL_NOT_SELF", "medium", "Canonical", "Listed URL canonicalises to another URL",
       "Only list canonical URLs in the sitemap, or make the page self-canonical if it should rank."),
    _d("PARAM_URL_NO_CANONICAL", "high", "Canonical", "Parameter URL has no canonical",
       "Add a canonical to the clean URL on filter, sort and tracking parameter variants."),
    _d("PARAM_URL_SELF_CANONICAL", "high", "Canonical", "Parameter URL canonicalises to itself",
       "Point faceted, sorted and tracking URLs at the clean URL, or noindex them."),
    _d("PAGINATION_CANONICAL_TO_FIRST", "medium", "Pagination", "Paginated page canonicalises to page 1",
       "Give each paginated page a self-referencing canonical so products on later pages stay discoverable."),
    # Meta
    _d("TITLE_MISSING", "high", "On-page", "Title tag missing", "Add a unique, descriptive title tag."),
    _d("TITLE_LONG", "low", "On-page", "Title is long and may be truncated",
       "Keep titles near 60 characters, with the product name first."),
    _d("META_DESC_MISSING", "medium", "On-page", "Meta description missing",
       "Write a unique meta description of roughly 120-160 characters."),
    _d("H1_MISSING", "medium", "On-page", "No H1 heading", "Add one H1 containing the product or category name."),
    _d("H1_MULTIPLE", "low", "On-page", "More than one H1 heading", "Use a single H1 per page."),
    _d("DUP_TITLE", "medium", "Duplicate content", "Title is shared with other pages",
       "Make titles unique: product name, key attribute and brand."),
    _d("DUP_DESCRIPTION", "medium", "Duplicate content", "Meta description is shared with other pages",
       "Write unique meta descriptions or let a template include the product name and attributes."),
    _d("THIN_DESCRIPTION", "medium", "Content", "Product description is thin",
       "Write a unique description covering materials, size, use cases and care; aim for 100+ words."),
    _d("IMAGE_ALT_LOW", "medium", "Images", "Low image alt-text coverage",
       "Add descriptive alt text to product images (name, colour, angle)."),
    # Breadcrumbs
    _d("BREADCRUMB_MISSING", "medium", "Structured data", "No BreadcrumbList markup",
       "Add BreadcrumbList structured data mirroring the visible breadcrumb trail."),
    _d("BREADCRUMB_INVALID", "medium", "Structured data", "BreadcrumbList markup is incomplete",
       "Each ListItem needs position and name, and every item except the last needs an item URL."),
    # hreflang
    _d("HREFLANG_INVALID_CODE", "medium", "hreflang", "Invalid hreflang language/region code",
       "Use ISO 639-1 language codes with optional ISO 3166-1 region, e.g. en-GB, or x-default."),
    _d("HREFLANG_NOT_ABSOLUTE", "medium", "hreflang", "hreflang URL is not absolute",
       "Use fully qualified URLs in hreflang annotations."),
    _d("HREFLANG_NO_SELF", "medium", "hreflang", "hreflang set lacks a self-reference",
       "Include the page itself in its hreflang set."),
    _d("HREFLANG_DUPLICATE", "medium", "hreflang", "Same hreflang code points to different URLs",
       "Each language/region code may map to only one URL."),
    _d("HREFLANG_NO_XDEFAULT", "info", "hreflang", "No x-default hreflang",
       "Consider adding an x-default for visitors who match no listed locale."),
    _d("HREFLANG_NO_RETURN", "medium", "hreflang", "hreflang target does not link back",
       "hreflang must be reciprocal: the alternate page has to reference this page too."),
]

CATALOG: Dict[str, IssueDef] = {d.code: d for d in _DEFS}


def make_issue(code: str, url: str, detail: str = "", severity: Optional[str] = None) -> Issue:
    d = CATALOG[code]
    return Issue(
        code=code,
        severity=severity or d.severity,
        category=d.category,
        title=d.title,
        fix=d.fix,
        url=url,
        detail=detail,
    )
