import pytest

from ecom_seo_audit.util import (
    find_amounts,
    first_page_url,
    is_tracking_or_facet,
    non_pagination_params,
    normalise_url,
    page_number,
    parameterised,
    parse_number,
    same_site,
    validate_gtin,
)


@pytest.mark.parametrize("text,expected", [
    ("1,299.00", 1299.0),
    ("1.299,00", 1299.0),
    ("12,50", 12.5),
    ("1 299,00", 1299.0),
    ("৳1,299", 1299.0),
    ("$ 12.50", 12.5),
    ("1,234,567", 1234567.0),
    ("1.234.567", 1234567.0),
    ("99", 99.0),
    ("-5.25", -5.25),
])
def test_parse_number(text, expected):
    assert parse_number(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "abc", None, "--"])
def test_parse_number_rejects(text):
    assert parse_number(text) is None


def test_find_amounts_range():
    assert find_amounts("Tk 100.00 – Tk 1,250.50") == [100.0, 1250.5]


def test_find_amounts_none():
    assert find_amounts("free") == []


@pytest.mark.parametrize("gtin", ["4006381333931", "036000291452", "96385074", "10614141000415"])
def test_gtin_valid(gtin):
    assert validate_gtin(gtin)[0]


def test_gtin_bad_check_digit():
    ok, why = validate_gtin("4006381333932")
    assert not ok and "check digit" in why


def test_gtin_bad_length():
    ok, why = validate_gtin("12345")
    assert not ok and "digits" in why


def test_gtin_non_digit():
    assert not validate_gtin("40063813339AB")[0]


def test_normalise_url_trailing_slash_and_case():
    assert normalise_url("HTTPS://Shop.Example.com:443/a/b/#x") == "https://shop.example.com/a/b"


def test_normalise_url_keeps_query():
    assert normalise_url("https://a.com/p/?x=1") == "https://a.com/p?x=1"


def test_same_site_ignores_www():
    assert same_site("https://www.a.com/x", "https://a.com/y")
    assert not same_site("https://a.com/x", "https://b.com/x")


def test_parameterised_ignores_pagination():
    assert not parameterised("https://a.com/c/?page=2")
    assert parameterised("https://a.com/c/?orderby=price")
    assert non_pagination_params("https://a.com/c/?paged=3&x=1") == ["x"]


@pytest.mark.parametrize("name,expected", [
    ("utm_source", True), ("filter_color", True), ("orderby", True), ("pa_size", True),
    ("filter.v.price.gte", True), ("foo", False),
])
def test_is_tracking_or_facet(name, expected):
    assert is_tracking_or_facet(name) is expected


def test_page_number_variants():
    assert page_number("https://a.com/c/page/3/") == 3
    assert page_number("https://a.com/c/?page=4") == 4
    assert page_number("https://a.com/c/") == 1


def test_first_page_url():
    assert first_page_url("https://a.com/c/page/3/") == "https://a.com/c/"
    assert first_page_url("https://a.com/c/?page=2&x=1") == "https://a.com/c/?x=1"
