import json
import os
from datetime import date

import pytest

from ecom_seo_audit.checks import AuditConfig
from ecom_seo_audit.extract import parse_page
from ecom_seo_audit.offline import load_offline

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "site")
URL = "https://shop.example.com/product/widget/"
WORDS = " ".join(["word"] * 80)


@pytest.fixture
def cfg():
    return AuditConfig(today=date(2026, 6, 1))


@pytest.fixture(scope="session")
def fixture_pages():
    return {u: parse_page(u, h) for u, h in load_offline(FIXTURES)}


def good_product(**over):
    product = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Widget",
        "image": "https://shop.example.com/w.jpg",
        "description": "A widget.",
        "sku": "W-1",
        "brand": {"@type": "Brand", "name": "Acme"},
        "offers": {
            "@type": "Offer",
            "price": "100.00",
            "priceCurrency": "USD",
            "availability": "https://schema.org/InStock",
            "priceValidUntil": "2027-01-01",
            "hasMerchantReturnPolicy": {"@type": "MerchantReturnPolicy"},
        },
    }
    product.update(over)
    return {k: v for k, v in product.items() if v is not None}


def make_html(product=None, head="", body=None, extra_ld=(), url=URL, canonical=True, title="Widget | Shop"):
    ld = ""
    if product is not None:
        ld += '<script type="application/ld+json">%s</script>' % json.dumps(product)
    for item in extra_ld:
        ld += '<script type="application/ld+json">%s</script>' % json.dumps(item)
    can = '<link rel="canonical" href="%s">' % url if canonical else ""
    if body is None:
        body = (
            '<h1>Widget</h1><img src="/w.jpg" alt="Widget"><span class="price">$100.00</span>'
            '<div class="product-description">%s</div>' % WORDS
        )
    return (
        "<html><head><title>%s</title><meta name=\"description\" content=\"A widget for sale.\">%s%s%s</head>"
        '<body class="single-product">%s</body></html>' % (title, can, ld, head, body)
    )


@pytest.fixture
def page_for(cfg):
    def build(product=None, **kw):
        url = kw.get("url", URL)
        headers = kw.pop("headers", None)
        status = kw.pop("status", 200)
        html = make_html(product, **kw)
        return parse_page(url, html, status=status, headers=headers)

    return build


@pytest.fixture
def codes(cfg):
    from ecom_seo_audit.checks import check_page

    def run(page):
        return {i.code for i in check_page(page, cfg)}

    return run
