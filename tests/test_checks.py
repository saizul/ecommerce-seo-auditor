import copy

import pytest

from conftest import URL, WORDS, good_product, make_html
from ecom_seo_audit.checks import check_page, check_site
from ecom_seo_audit.extract import parse_page


def offer(**over):
    base = copy.deepcopy(good_product()["offers"])
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def with_offer(**over):
    return good_product(offers=offer(**over))


# ------------------------------------------------------------------ baseline
def test_clean_product_has_no_issues(page_for, cfg):
    page = page_for(good_product(), extra_ld=[{
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://shop.example.com/"},
            {"@type": "ListItem", "position": 2, "name": "Widget"},
        ],
    }])
    assert [i.code for i in check_page(page, cfg)] == []


def test_fixture_good_product_is_clean(fixture_pages, cfg):
    page = fixture_pages["https://shop.example.com/product/linen-shirt/"]
    assert check_page(page, cfg) == []


# ------------------------------------------------------------------ fetch / http
def test_http_error(cfg):
    page = parse_page(URL, "", status=404)
    assert [i.code for i in check_page(page, cfg)] == ["HTTP_ERROR"]


def test_fetch_failed(cfg):
    from ecom_seo_audit.extract import Page
    page = Page(url=URL, error="timeout")
    assert [i.code for i in check_page(page, cfg)] == ["FETCH_FAILED"]


def test_redirect_in_list(cfg):
    html = make_html(good_product())
    page = parse_page("https://shop.example.com/product/new/", html, request_url=URL)
    assert "REDIRECT_IN_LIST" in {i.code for i in check_page(page, cfg)}


# ------------------------------------------------------------------ product markup
def test_schema_missing(page_for, codes):
    assert "SCHEMA_MISSING" in codes(page_for(None))


def test_schema_jsonld_invalid(codes):
    html = make_html(None, head='<script type="application/ld+json">{oops</script>')
    assert {"SCHEMA_JSONLD_INVALID", "SCHEMA_MISSING"} <= codes(parse_page(URL, html))


def test_schema_duplicate_markup(codes):
    body = ('<h1>W</h1><div itemscope itemtype="https://schema.org/Product">'
            '<span itemprop="name">Widget</span></div>')
    page = parse_page(URL, make_html(good_product(), body=body))
    assert "SCHEMA_DUPLICATE_MARKUP" in codes(page)


@pytest.mark.parametrize("field,code", [
    ("name", "PRODUCT_NAME_MISSING"),
    ("image", "PRODUCT_IMAGE_MISSING"),
    ("description", "PRODUCT_DESCRIPTION_MISSING"),
    ("brand", "BRAND_MISSING"),
])
def test_missing_core_fields(page_for, codes, field, code):
    product = good_product()
    del product[field]
    assert code in codes(page_for(product))


def test_identifier_missing(page_for, codes):
    product = good_product()
    del product["sku"]
    assert "IDENTIFIER_MISSING" in codes(page_for(product))


def test_identifier_ok_with_gtin_only(page_for, codes):
    product = good_product(gtin13="4006381333931")
    del product["sku"]
    assert "IDENTIFIER_MISSING" not in codes(page_for(product))


def test_gtin_invalid(page_for, cfg):
    page = page_for(good_product(gtin13="4006381333932"))
    issue = [i for i in check_page(page, cfg) if i.code == "GTIN_INVALID"][0]
    assert "check digit" in issue.detail


def test_gtin_valid_not_flagged(page_for, codes):
    assert "GTIN_INVALID" not in codes(page_for(good_product(gtin13="4006381333931")))


def test_offer_missing(page_for, codes):
    product = good_product()
    del product["offers"]
    assert "OFFER_MISSING" in codes(page_for(product))


def test_offer_price_missing(page_for, codes):
    assert "OFFER_PRICE_MISSING" in codes(page_for(with_offer(price=None)))


@pytest.mark.parametrize("price", ["1,299.00", "Tk 100", "abc", "-5"])
def test_price_invalid(page_for, codes, price):
    assert "PRICE_INVALID" in codes(page_for(with_offer(price=price)))


def test_price_numeric_json_number_ok(page_for, codes):
    assert "PRICE_INVALID" not in codes(page_for(with_offer(price=100.5)))


def test_price_zero(page_for, codes):
    assert "PRICE_ZERO" in codes(page_for(with_offer(price="0")))


def test_currency_missing_and_invalid(page_for, codes):
    assert "PRICE_CURRENCY_MISSING" in codes(page_for(with_offer(priceCurrency=None)))
    assert "PRICE_CURRENCY_INVALID" in codes(page_for(with_offer(priceCurrency="usd")))
    assert "PRICE_CURRENCY_INVALID" in codes(page_for(with_offer(priceCurrency="TAKA")))


def test_availability_missing_and_invalid(page_for, codes):
    assert "AVAILABILITY_MISSING" in codes(page_for(with_offer(availability=None)))
    assert "AVAILABILITY_INVALID" in codes(page_for(with_offer(availability="In stock")))


@pytest.mark.parametrize("value", ["InStock", "http://schema.org/InStock", "schema:InStock"])
def test_availability_forms_accepted(page_for, codes, value):
    page = page_for(with_offer(availability=value))
    c = codes(page)
    assert "AVAILABILITY_INVALID" not in c and "STOCK_MISMATCH" not in c


def test_price_valid_until(page_for, codes):
    assert "PRICE_VALID_UNTIL_MISSING" in codes(page_for(with_offer(priceValidUntil=None)))
    assert "PRICE_VALID_UNTIL_INVALID" in codes(page_for(with_offer(priceValidUntil="next year")))
    assert "PRICE_VALID_UNTIL_INVALID" in codes(page_for(with_offer(priceValidUntil="2027-13-45")))
    assert "PRICE_VALID_UNTIL_EXPIRED" in codes(page_for(with_offer(priceValidUntil="2025-12-31")))


def test_shipping_return_missing(page_for, codes):
    product = with_offer(hasMerchantReturnPolicy=None)
    assert "SHIPPING_RETURN_MISSING" in codes(page_for(product))


def test_aggregate_offer_supported(page_for, codes):
    product = good_product(offers={
        "@type": "AggregateOffer", "lowPrice": "100.00", "highPrice": "150.00", "priceCurrency": "USD",
        "offers": [offer(price="100.00"), offer(price="150.00", priceCurrency=None)],
    })
    c = codes(page_for(product))
    assert not c & {"OFFER_PRICE_MISSING", "PRICE_CURRENCY_MISSING", "OFFER_MISSING"}


def test_product_group_variants_provide_offers(page_for, codes):
    product = {
        "@type": "ProductGroup", "name": "Widget", "productGroupID": "G1", "brand": {"name": "Acme"},
        "description": "x", "image": "https://x/i.jpg",
        "hasVariant": [dict(good_product(), **{"@type": "Product"})],
    }
    c = codes(page_for(product))
    assert not c & {"OFFER_MISSING", "IDENTIFIER_MISSING", "BRAND_MISSING"}


def test_price_specification_price_read(page_for, codes):
    o = offer(price=None)
    o["priceSpecification"] = {"@type": "PriceSpecification", "price": "100.00", "priceCurrency": "USD"}
    assert "OFFER_PRICE_MISSING" not in codes(page_for(good_product(offers=o)))


# ------------------------------------------------------------------ price & stock consistency
def test_price_mismatch(page_for, cfg):
    page = page_for(good_product(), body='<h1>W</h1><span class="price">$120.00</span>')
    issue = [i for i in check_page(page, cfg) if i.code == "PRICE_MISMATCH"][0]
    assert "100.00" in issue.detail and "120.00" in issue.detail


def test_price_match_with_thousands_separator(page_for, codes):
    product = with_offer(price="1299.00")
    page = page_for(product, body='<h1>W</h1><span class="price">Tk 1,299.00</span>')
    assert "PRICE_MISMATCH" not in codes(page)


def test_price_match_any_in_range(page_for, codes):
    page = page_for(good_product(), body='<h1>W</h1><p class="price">$100.00 – $150.00</p>')
    assert "PRICE_MISMATCH" not in codes(page)


def test_price_not_found(page_for, codes):
    page = page_for(good_product(), body="<h1>W</h1>")
    assert "PRICE_NOT_FOUND_ON_PAGE" in codes(page)


def test_price_mismatch_ignores_struck_through_regular_price(page_for, codes):
    body = '<h1>W</h1><p class="price"><del>$150.00</del> <ins>$100.00</ins></p>'
    assert "PRICE_MISMATCH" not in codes(page_for(good_product(), body=body))


def test_meta_price_counts_as_visible(page_for, codes):
    page = page_for(good_product(), body="<h1>W</h1>", head='<meta property="product:price:amount" content="100.00">')
    c = codes(page)
    assert "PRICE_NOT_FOUND_ON_PAGE" not in c and "PRICE_MISMATCH" not in c


def test_stock_mismatch_markup_in_page_out(page_for, codes):
    body = '<h1>W</h1><span class="price">$100.00</span><p class="stock out-of-stock">Out of stock</p>'
    assert "STOCK_MISMATCH" in codes(page_for(good_product(), body=body))


def test_stock_mismatch_markup_out_page_in(page_for, codes):
    body = '<h1>W</h1><span class="price">$100.00</span><button>Add to cart</button>'
    product = with_offer(availability="https://schema.org/OutOfStock")
    assert "STOCK_MISMATCH" in codes(page_for(product, body=body))


def test_stock_consistent_out(page_for, codes):
    body = '<h1>W</h1><span class="price">$100.00</span><p class="stock out-of-stock">Sold out</p>'
    product = with_offer(availability="https://schema.org/OutOfStock")
    assert "STOCK_MISMATCH" not in codes(page_for(product, body=body))


def test_stock_mismatch_fixture(fixture_pages, cfg):
    page = fixture_pages["https://store.example.com/products/leather-wallet"]
    found = {i.code for i in check_page(page, cfg)}
    assert {"STOCK_MISMATCH", "PRICE_MISMATCH"} <= found


# ------------------------------------------------------------------ ratings / reviews
def agg(**over):
    base = {"@type": "AggregateRating", "ratingValue": "4.5", "reviewCount": "10"}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def test_rating_ok(page_for, codes):
    c = codes(page_for(good_product(aggregateRating=agg())))
    assert not c & {"RATING_INVALID", "RATING_COUNT_MISSING", "RATING_COUNT_ZERO"}


def test_rating_value_out_of_range(page_for, codes):
    assert "RATING_INVALID" in codes(page_for(good_product(aggregateRating=agg(ratingValue="7"))))


def test_rating_value_missing(page_for, codes):
    assert "RATING_INVALID" in codes(page_for(good_product(aggregateRating=agg(ratingValue=None))))


def test_rating_custom_best_rating(page_for, codes):
    page = page_for(good_product(aggregateRating=agg(ratingValue="8", bestRating="10")))
    assert "RATING_INVALID" not in codes(page)


def test_rating_count_missing(page_for, codes):
    assert "RATING_COUNT_MISSING" in codes(page_for(good_product(aggregateRating=agg(reviewCount=None))))


def test_rating_count_alternative_rating_count(page_for, codes):
    page = page_for(good_product(aggregateRating=agg(reviewCount=None, ratingCount="5")))
    assert "RATING_COUNT_MISSING" not in codes(page)


def test_rating_count_zero(page_for, codes):
    assert "RATING_COUNT_ZERO" in codes(page_for(good_product(aggregateRating=agg(reviewCount="0"))))


def test_review_invalid_and_count_inconsistent(fixture_pages, cfg):
    page = fixture_pages["https://shop.example.com/product/ceramic-mug/"]
    found = {i.code for i in check_page(page, cfg)}
    assert {"REVIEW_INVALID", "REVIEW_COUNT_INCONSISTENT"} <= found


def test_review_valid(page_for, codes):
    review = {"@type": "Review", "author": {"@type": "Person", "name": "A"},
              "reviewRating": {"@type": "Rating", "ratingValue": "5"}}
    page = page_for(good_product(aggregateRating=agg(), review=[review]))
    assert "REVIEW_INVALID" not in codes(page)


def test_rating_visible_mismatch(page_for, codes):
    body = '<h1>W</h1><span class="price">$100.00</span><h2>Reviews (3)</h2>'
    assert "RATING_VISIBLE_MISMATCH" in codes(page_for(good_product(aggregateRating=agg()), body=body))


# ------------------------------------------------------------------ canonical / indexing
def test_canonical_missing(page_for, codes):
    assert "CANONICAL_MISSING" in codes(page_for(good_product(), canonical=False))


def test_canonical_multiple(page_for, codes):
    page = page_for(good_product(), head='<link rel="canonical" href="https://shop.example.com/other/">')
    assert "CANONICAL_MULTIPLE" in codes(page)


def test_canonical_offsite(page_for, codes):
    page = page_for(good_product(), canonical=False, head='<link rel="canonical" href="https://other.com/p/">')
    assert "CANONICAL_OFFSITE" in codes(page)


def test_canonical_relative(page_for, codes):
    page = page_for(good_product(), canonical=False, head='<link rel="canonical" href="/product/widget/">')
    assert "CANONICAL_RELATIVE" in codes(page)


def test_canonical_not_self(page_for, codes):
    page = page_for(good_product(), canonical=False,
                    head='<link rel="canonical" href="https://shop.example.com/product/other/">')
    assert "CANONICAL_NOT_SELF" in codes(page)


def test_canonical_www_difference_counts_as_same_site_but_not_self(page_for, codes):
    page = page_for(good_product(), canonical=False,
                    head='<link rel="canonical" href="https://www.shop.example.com/product/widget/">')
    c = codes(page)
    assert "CANONICAL_OFFSITE" not in c and "CANONICAL_NOT_SELF" in c


def test_param_url_no_canonical(page_for, codes):
    url = URL + "?utm_source=mail"
    assert "PARAM_URL_NO_CANONICAL" in codes(page_for(good_product(), url=url, canonical=False))


def test_param_url_self_canonical(fixture_pages, cfg):
    page = fixture_pages["https://shop.example.com/product-category/shirts/?orderby=price&filter_color=red"]
    assert "PARAM_URL_SELF_CANONICAL" in {i.code for i in check_page(page, cfg)}


def test_param_url_canonical_to_clean_is_fine(page_for, codes):
    url = URL + "?utm_source=mail"
    page = page_for(good_product(), url=url, canonical=False, head='<link rel="canonical" href="%s">' % URL)
    c = codes(page)
    assert not c & {"PARAM_URL_NO_CANONICAL", "PARAM_URL_SELF_CANONICAL", "CANONICAL_NOT_SELF"}


def test_param_url_self_canonical_but_noindex_ok(page_for, codes):
    url = URL + "?orderby=price"
    page = page_for(good_product(), url=url, head='<meta name="robots" content="noindex">')
    assert "PARAM_URL_SELF_CANONICAL" not in codes(page)


def test_pagination_canonical_to_first(fixture_pages, cfg):
    page = fixture_pages["https://shop.example.com/product-category/shirts/page/2/"]
    assert "PAGINATION_CANONICAL_TO_FIRST" in {i.code for i in check_page(page, cfg)}


def test_paginated_self_canonical_ok(cfg):
    url = "https://shop.example.com/product-category/shirts/page/2/"
    html = '<html><head><title>t</title><link rel="canonical" href="%s"></head><body class="tax-product_cat"><h1>S</h1></body></html>' % url
    found = {i.code for i in check_page(parse_page(url, html), cfg)}
    assert "PAGINATION_CANONICAL_TO_FIRST" not in found and "CANONICAL_NOT_SELF" not in found


def test_noindex_product(fixture_pages, cfg):
    page = fixture_pages["https://shop.example.com/product/hidden-item/"]
    found = {i.code for i in check_page(page, cfg)}
    assert "NOINDEX_PRODUCT" in found


def test_noindex_via_header(page_for, codes):
    page = page_for(good_product(), headers={"x-robots-tag": "noindex"})
    assert "NOINDEX_PRODUCT" in codes(page)


def test_noindex_category(cfg):
    html = '<html><head><title>t</title><meta name="robots" content="noindex"></head><body class="tax-product_cat"></body></html>'
    page = parse_page("https://shop.example.com/product-category/a/", html)
    assert "NOINDEX_CATEGORY" in {i.code for i in check_page(page, cfg)}


def test_noindex_on_param_url_not_flagged(cfg):
    html = '<html><head><title>t</title><meta name="robots" content="noindex"></head><body class="tax-product_cat"></body></html>'
    page = parse_page("https://shop.example.com/product-category/a/?filter_color=red", html)
    assert "NOINDEX_CATEGORY" not in {i.code for i in check_page(page, cfg)}


# ------------------------------------------------------------------ on-page / content
def test_title_missing_and_long(page_for, codes):
    assert "TITLE_MISSING" in codes(page_for(good_product(), title=""))
    assert "TITLE_LONG" in codes(page_for(good_product(), title="x" * 80))


def test_meta_description_missing(codes):
    html = make_html(good_product()).replace('<meta name="description" content="A widget for sale.">', "")
    assert "META_DESC_MISSING" in codes(parse_page(URL, html))


def test_h1_checks(page_for, codes):
    assert "H1_MISSING" in codes(page_for(good_product(), body="<p>x</p>"))
    assert "H1_MULTIPLE" in codes(page_for(good_product(), body="<h1>a</h1><h1>b</h1>"))


def test_thin_description(page_for, cfg):
    page = page_for(good_product(), body='<h1>W</h1><span class="price">$100.00</span><div class="product-description">Short.</div>')
    issue = [i for i in check_page(page, cfg) if i.code == "THIN_DESCRIPTION"][0]
    assert "1 words" in issue.detail


def test_description_falls_back_to_schema(page_for, codes):
    product = good_product(description=WORDS)
    body = '<h1>W</h1><span class="price">$100.00</span>'
    assert "THIN_DESCRIPTION" not in codes(page_for(product, body=body))


def test_image_alt_low(page_for, cfg):
    body = ('<h1>W</h1><span class="price">$100.00</span><img src="a.jpg"><img src="b.jpg" alt="">'
            '<img src="c.jpg" alt="ok"><div class="product-description">%s</div>' % WORDS)
    issue = [i for i in check_page(page_for(good_product(), body=body), cfg) if i.code == "IMAGE_ALT_LOW"][0]
    assert "1 of 3" in issue.detail


def test_image_alt_threshold_configurable(page_for, cfg):
    cfg.alt_threshold = 0.3
    body = ('<h1>W</h1><span class="price">$100.00</span><img src="a.jpg"><img src="b.jpg" alt="x">'
            '<img src="c.jpg" alt="y"><div class="product-description">%s</div>' % WORDS)
    assert "IMAGE_ALT_LOW" not in {i.code for i in check_page(page_for(good_product(), body=body), cfg)}


# ------------------------------------------------------------------ breadcrumbs
def crumbs(*items):
    return {"@type": "BreadcrumbList", "itemListElement": list(items)}


def li(pos, name, item=None):
    d = {"@type": "ListItem", "position": pos, "name": name}
    if item:
        d["item"] = item
    return {k: v for k, v in d.items() if v is not None}


def test_breadcrumb_missing(page_for, codes):
    assert "BREADCRUMB_MISSING" in codes(page_for(good_product()))


def test_breadcrumb_valid(page_for, codes):
    page = page_for(good_product(), extra_ld=[crumbs(li(1, "Home", "https://a.com/"), li(2, "Widget"))])
    c = codes(page)
    assert not c & {"BREADCRUMB_MISSING", "BREADCRUMB_INVALID"}


def test_breadcrumb_too_short(page_for, codes):
    assert "BREADCRUMB_INVALID" in codes(page_for(good_product(), extra_ld=[crumbs(li(1, "Home", "https://a.com/"))]))


def test_breadcrumb_missing_item_url(page_for, codes):
    page = page_for(good_product(), extra_ld=[crumbs(li(1, "Home"), li(2, "Widget"))])
    assert "BREADCRUMB_INVALID" in codes(page)


def test_breadcrumb_missing_name_and_position(page_for, cfg):
    bad = crumbs({"@type": "ListItem", "item": "https://a.com/"}, li(2, "Widget"))
    issues = check_page(page_for(good_product(), extra_ld=[bad]), cfg)
    detail = [i for i in issues if i.code == "BREADCRUMB_INVALID"][0].detail
    assert "no position" in detail and "no name" in detail


def test_breadcrumb_item_object_with_name(page_for, codes):
    first = {"@type": "ListItem", "position": 1, "item": {"@id": "https://a.com/", "name": "Home"}}
    page = page_for(good_product(), extra_ld=[crumbs(first, li(2, "Widget"))])
    assert "BREADCRUMB_INVALID" not in codes(page)


def test_breadcrumb_microdata_supported(codes):
    body = ('<h1>W</h1><span class="price">$100.00</span><ol itemscope itemtype="https://schema.org/BreadcrumbList">'
            '<li itemprop="itemListElement" itemscope itemtype="https://schema.org/ListItem">'
            '<a itemprop="item" href="/"><span itemprop="name">Home</span></a><meta itemprop="position" content="1"></li>'
            '<li itemprop="itemListElement" itemscope itemtype="https://schema.org/ListItem">'
            '<span itemprop="name">Widget</span><meta itemprop="position" content="2"></li></ol>')
    page = parse_page(URL, make_html(good_product(), body=body))
    c = codes(page)
    assert not c & {"BREADCRUMB_MISSING", "BREADCRUMB_INVALID"}


# ------------------------------------------------------------------ hreflang
def test_hreflang_issues_on_fixture(fixture_pages, cfg):
    page = fixture_pages["https://shop.example.com/product/scarf/"]
    found = {i.code for i in check_page(page, cfg)}
    assert {"HREFLANG_INVALID_CODE", "HREFLANG_NOT_ABSOLUTE", "HREFLANG_NO_XDEFAULT"} <= found


def test_hreflang_no_self(page_for, codes):
    head = '<link rel="alternate" hreflang="bn" href="https://shop.example.com/bn/p/"><link rel="alternate" hreflang="x-default" href="https://shop.example.com/bn/p/">'
    assert "HREFLANG_NO_SELF" in codes(page_for(good_product(), head=head))


def test_hreflang_duplicate_code(page_for, codes):
    head = ('<link rel="alternate" hreflang="en" href="%s"><link rel="alternate" hreflang="en" href="https://shop.example.com/x/">' % URL)
    assert "HREFLANG_DUPLICATE" in codes(page_for(good_product(), head=head))


def test_hreflang_valid_set_clean(page_for, codes):
    head = ('<link rel="alternate" hreflang="en" href="%s"><link rel="alternate" hreflang="x-default" href="%s">' % (URL, URL))
    assert not {c for c in codes(page_for(good_product(), head=head)) if c.startswith("HREFLANG")}


def test_hreflang_return_links(cfg):
    a = parse_page("https://a.com/en/", '<link rel="alternate" hreflang="en" href="https://a.com/en/">'
                                         '<link rel="alternate" hreflang="fr" href="https://a.com/fr/">', page_type="product")
    b = parse_page("https://a.com/fr/", '<link rel="alternate" hreflang="fr" href="https://a.com/fr/">', page_type="product")
    result = check_site([a, b])
    assert [i.code for i in result["https://a.com/en/"]] == ["HREFLANG_NO_RETURN"]
    assert "https://a.com/fr/" not in result


# ------------------------------------------------------------------ site-wide duplicates
def test_duplicate_titles_and_descriptions(fixture_pages):
    result = check_site(list(fixture_pages.values()))
    dup_urls = {u for u, issues in result.items() if any(i.code == "DUP_TITLE" for i in issues)}
    assert dup_urls == {"https://shop.example.com/product/cotton-tee/", "https://shop.example.com/product/cotton-tee-v2/"}


def test_duplicates_skip_canonicalised_and_noindex_pages():
    def pg(url, canonical=None, robots=None):
        head = "<title>Same</title><meta name='description' content='Same desc'>"
        if canonical:
            head += '<link rel="canonical" href="%s">' % canonical
        if robots:
            head += '<meta name="robots" content="%s">' % robots
        return parse_page(url, "<html><head>%s</head><body></body></html>" % head, page_type="product")

    pages = [pg("https://a.com/p1"), pg("https://a.com/p2", canonical="https://a.com/p1"),
             pg("https://a.com/p3", robots="noindex")]
    assert check_site(pages) == {}


def test_duplicate_title_is_case_and_space_insensitive():
    mk = lambda u, t: parse_page(u, "<title>%s</title>" % t, page_type="product")  # noqa: E731
    result = check_site([mk("https://a.com/1", "Blue  Shirt"), mk("https://a.com/2", "blue shirt")])
    assert len(result) == 2
