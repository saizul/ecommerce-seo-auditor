import json

from bs4 import BeautifulSoup

from conftest import make_html
from ecom_seo_audit.extract import extract_jsonld, extract_microdata, parse_page, types_of


def soup(html):
    return BeautifulSoup(html, "html.parser")


def test_jsonld_graph_flattened_and_refs_resolved(fixture_pages):
    page = fixture_pages["https://shop.example.com/product/linen-shirt/"]
    product = page.products[0].data
    assert product["offers"]["price"] == "1299.00"
    assert page.breadcrumbs and page.breadcrumbs[0].source == "jsonld"


def test_jsonld_invalid_counted():
    nodes, errors = extract_jsonld(soup('<script type="application/ld+json">{bad json</script>'))
    assert nodes == [] and errors == 1


def test_jsonld_trailing_comma_tolerated():
    nodes, errors = extract_jsonld(soup('<script type="application/ld+json">{"@type":"Product","name":"x",}</script>'))
    assert errors == 0 and nodes[0]["name"] == "x"


def test_jsonld_list_root():
    html = '<script type="application/ld+json">[{"@type":"Product"},{"@type":"Organization"}]</script>'
    nodes, _ = extract_jsonld(soup(html))
    assert [types_of(n)[0] for n in nodes] == ["Product", "Organization"]


def test_jsonld_main_entity_walked():
    html = '<script type="application/ld+json">{"@type":"WebPage","mainEntity":{"@type":"Product","name":"p"}}</script>'
    nodes, _ = extract_jsonld(soup(html))
    assert any(types_of(n) == ["Product"] for n in nodes)


def test_types_of_handles_lists_and_urls():
    assert types_of({"@type": ["Product", "https://schema.org/Thing"]}) == ["Product", "Thing"]


def test_microdata_product_properties(fixture_pages):
    page = fixture_pages["https://shop.example.com/product/ceramic-mug/"]
    p = page.products[0].data
    assert p["name"] == "Ceramic Mug"
    assert p["brand"]["name"] == "Kiln & Co"
    assert p["offers"]["price"] == "18.50"
    assert p["offers"]["availability"].endswith("OutOfStock")
    assert len(p["review"]) == 3


def test_microdata_ignores_related_blocks():
    html = (
        '<div class="related"><div itemscope itemtype="https://schema.org/Product">'
        '<span itemprop="name">Other</span></div></div>'
    )
    assert extract_microdata(soup(html)) == []


def test_title_description_canonical(fixture_pages):
    page = fixture_pages["https://shop.example.com/product/linen-shirt/"]
    assert page.title == "Linen Shirt | Example Shop"
    assert page.meta_description.startswith("A breathable linen shirt")
    assert page.canonical_urls == ["https://shop.example.com/product/linen-shirt/"]


def test_relative_canonical_resolved():
    page = parse_page("https://a.com/p/", '<link rel="canonical" href="/p/">')
    assert page.canonicals == ["/p/"] and page.canonical_urls == ["https://a.com/p/"]


def test_hreflang_extracted(fixture_pages):
    page = fixture_pages["https://shop.example.com/product/scarf/"]
    assert [c for c, _, _ in page.hreflangs] == ["en", "bn-BD", "english"]
    assert page.hreflangs[2][2] == "https://shop.example.com/en/product/scarf/"


def test_robots_meta_and_header():
    page = parse_page("https://a.com/", '<meta name="robots" content="noindex,follow">')
    assert page.noindex
    page = parse_page("https://a.com/", "<html></html>", headers={"X-Robots-Tag": "noindex"})
    assert page.noindex
    page = parse_page("https://a.com/", '<meta name="googlebot" content="none">')
    assert page.noindex


def test_visible_price_skips_struck_and_regular_prices(fixture_pages):
    page = fixture_pages["https://store.example.com/products/leather-wallet"]
    assert page.visible_prices == [2799.0]


def test_visible_price_woocommerce_symbol(fixture_pages):
    page = fixture_pages["https://shop.example.com/product/linen-shirt/"]
    assert page.visible_prices == [1299.0]


def test_related_products_pruned_from_text_and_price(fixture_pages):
    page = fixture_pages["https://shop.example.com/product/linen-shirt/"]
    assert 999.0 not in page.visible_prices
    assert page.stock_signal == "in"


def test_meta_price_extracted():
    page = parse_page("https://a.com/p", '<meta property="product:price:amount" content="19.99">')
    assert page.meta_price == 19.99


def test_stock_signal_out_by_class_and_text(fixture_pages):
    assert fixture_pages["https://shop.example.com/product/ceramic-mug/"].stock_signal == "out"
    assert fixture_pages["https://store.example.com/products/leather-wallet"].stock_signal == "out"


def test_stock_signal_none_when_unknown():
    assert parse_page("https://a.com/", "<p>hello</p>").stock_signal is None


def test_stock_signal_ambiguous_text_returns_none():
    page = parse_page("https://a.com/", "<p>Sold out in blue. Add to cart in red.</p>")
    assert page.stock_signal is None


def test_images_skip_decorative_and_pixels():
    html = (
        '<img src="a.jpg" alt="A"><img src="b.jpg"><img src="c.jpg" role="presentation">'
        '<img src="t.gif" width="1" height="1"><img data-src="lazy.jpg" alt="">'
    )
    page = parse_page("https://a.com/", html)
    assert [(i.src, i.alt) for i in page.images] == [("a.jpg", "A"), ("b.jpg", None), ("lazy.jpg", "")]


def test_footer_and_header_images_ignored(fixture_pages):
    page = fixture_pages["https://shop.example.com/product/linen-shirt/"]
    assert [i.src for i in page.images] == ["/img/linen-shirt.jpg", "/img/linen-shirt-2.jpg"]


def test_description_picks_longest_block(fixture_pages):
    page = fixture_pages["https://shop.example.com/product/linen-shirt/"]
    assert page.description_text.startswith("This linen shirt is woven")


def test_visible_review_count(fixture_pages):
    assert fixture_pages["https://shop.example.com/product/linen-shirt/"].visible_review_count == 12


def test_h1s(fixture_pages):
    assert len(fixture_pages["https://shop.example.com/product/cotton-tee/"].h1s) == 2


def test_page_type_detection(fixture_pages):
    f = fixture_pages
    assert f["https://shop.example.com/product/linen-shirt/"].page_type == "product"
    assert f["https://store.example.com/products/leather-wallet"].page_type == "product"
    assert f["https://shop.example.com/product-category/shirts/page/2/"].page_type == "category"


def test_page_type_override():
    page = parse_page("https://a.com/x", "<html></html>", page_type="category")
    assert page.page_type == "category"


def test_page_type_other_for_plain_page():
    assert parse_page("https://a.com/about", "<html><body>hi</body></html>").page_type == "other"


def test_pagination_detected(fixture_pages):
    assert fixture_pages["https://shop.example.com/product-category/shirts/page/2/"].has_pagination


def test_make_html_roundtrip():
    page = parse_page("https://shop.example.com/product/widget/", make_html({"@type": "Product", "name": "W"}))
    assert json.dumps(page.products[0].data["name"]) == '"W"'
