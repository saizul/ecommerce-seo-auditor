import csv
import io
import json
import os

import pytest

from conftest import FIXTURES, URL, good_product, make_html
from ecom_seo_audit.audit import build_fixes, grade_for, run_audit, score_issues
from ecom_seo_audit.checks import AuditConfig
from ecom_seo_audit.cli import main
from ecom_seo_audit.extract import Page, parse_page
from ecom_seo_audit.issues import CATALOG, SEVERITY_WEIGHTS, make_issue
from ecom_seo_audit.report import to_csv, to_dict, to_html, to_json, write_reports


@pytest.fixture
def result(fixture_pages, cfg):
    return run_audit(list(fixture_pages.values()), cfg)


# ------------------------------------------------------------------ catalog / scoring
def test_catalog_entries_complete():
    assert len(CATALOG) >= 55
    for d in CATALOG.values():
        assert d.severity in SEVERITY_WEIGHTS and d.fix and d.title and d.category


def test_make_issue_severity_override():
    assert make_issue("TITLE_LONG", "u", severity="high").severity == "high"


def test_score_issues():
    issues = [make_issue("NOINDEX_PRODUCT", "u"), make_issue("TITLE_LONG", "u")]
    assert score_issues(issues) == 100 - 25 - 2


def test_score_floor_zero():
    issues = [make_issue("NOINDEX_PRODUCT", "u")] * 5
    assert score_issues(issues) == 0


@pytest.mark.parametrize("score,grade", [(95, "A"), (85, "B"), (72, "C"), (60, "D"), (20, "F")])
def test_grades(score, grade):
    assert grade_for(score) == grade


def test_run_audit_deduplicates_codes(cfg):
    page = parse_page(URL, make_html(good_product(offers=None)))
    res = run_audit([page], cfg)
    codes = [i.code for i in res.pages[0].issues]
    assert len(codes) == len(set(codes))


def test_issues_sorted_by_severity(result):
    order = ["critical", "high", "medium", "low", "info"]
    for p in result.pages:
        ranks = [order.index(i.severity) for i in p.issues]
        assert ranks == sorted(ranks)


def test_site_score_is_mean(result):
    mean = sum(p.score for p in result.pages) / len(result.pages)
    assert result.score == pytest.approx(round(mean, 1))


def test_fixes_prioritised_by_weight_times_pages(result):
    priorities = [f.priority for f in result.fixes]
    assert priorities == sorted(priorities, reverse=True)
    top = result.fixes[0]
    assert top.pages_affected >= 1 and top.examples


def test_fix_score_gain(result):
    fix = next(f for f in result.fixes if f.code == "NOINDEX_PRODUCT")
    assert fix.score_gain == pytest.approx(25 * fix.pages_affected / result.total_pages, abs=0.01)


def test_blocked_pages_skipped_not_scored(cfg):
    blocked = Page(url="https://a.com/x", blocked=True, error="blocked by robots.txt")
    ok = parse_page(URL, make_html(good_product()))
    res = run_audit([blocked, ok], cfg)
    assert len(res.skipped) == 1 and res.total_pages == 1


def test_empty_audit(cfg):
    res = run_audit([], cfg)
    assert res.score == 0.0 and res.pages == []


def test_build_fixes_empty():
    assert build_fixes([]) == []


def test_page_type_override_applied_to_checks(cfg):
    html = "<html><head><title>x</title></head><body>hi</body></html>"
    page = parse_page("https://a.com/about", html, page_type="product")
    assert "SCHEMA_MISSING" in {i.code for i in run_audit([page], cfg).pages[0].issues}


# ------------------------------------------------------------------ reports
def test_json_report_structure(result):
    data = json.loads(to_json(result))
    assert data["tool"] == "ecom-seo-audit"
    assert data["summary"]["pages_audited"] == len(result.pages)
    assert data["fixes"][0]["rank"] == 1
    assert {"url", "type", "score", "issues"} <= set(data["pages"][0])


def test_to_dict_matches_json(result):
    assert json.loads(to_json(result)) == json.loads(json.dumps(to_dict(result)))


def test_csv_report_rows(result):
    rows = list(csv.DictReader(io.StringIO(to_csv(result))))
    assert len(rows) == sum(len(p.issues) for p in result.pages)
    assert {"url", "severity", "code", "fix"} <= set(rows[0])


def test_csv_formula_injection_neutralised(cfg):
    page = parse_page("https://shop.example.com/product/x/", make_html(good_product(gtin13="=1+1"), url="https://shop.example.com/product/x/"))
    res = run_audit([page], cfg)
    rows = list(csv.DictReader(io.StringIO(to_csv(res))))
    gtin = [r for r in rows if r["code"] == "GTIN_INVALID"][0]
    assert gtin["detail"].startswith("gtin13") and not gtin["detail"].startswith("=")
    res.pages[0].issues[0].detail = "=HYPERLINK(\"http://evil\")"
    rows = list(csv.DictReader(io.StringIO(to_csv(res))))
    assert rows[0]["detail"].startswith("'=")


def test_html_report_contains_sections(result):
    html = to_html(result)
    assert "Prioritised fixes" in html and "Site score" in html and "PRICE_MISMATCH" in html


def test_html_report_escapes_markup(cfg):
    page = parse_page("https://a.com/product/<script>/", make_html(good_product(offers=None), url="https://a.com/p/"))
    page.url = "https://a.com/product/<script>alert(1)</script>/"
    html = to_html(run_audit([page], cfg))
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_write_reports_creates_files(result, tmp_path):
    paths = write_reports(result, str(tmp_path / "out"), ["html", "csv", "json"])
    assert [os.path.basename(p) for p in paths] == ["report.html", "report.csv", "report.json"]
    assert all(os.path.getsize(p) > 0 for p in paths)


# ------------------------------------------------------------------ CLI
def test_cli_offline_end_to_end(tmp_path, capsys):
    out = str(tmp_path / "rep")
    code = main(["--offline", FIXTURES, "--out", out, "--quiet"])
    assert code == 0
    data = json.load(open(os.path.join(out, "report.json")))
    assert data["summary"]["pages_audited"] == 10
    assert os.path.exists(os.path.join(out, "report.html"))


def test_cli_min_score_fails(tmp_path):
    assert main(["--offline", FIXTURES, "--out", str(tmp_path), "--min-score", "99", "--quiet"]) == 1


def test_cli_min_score_passes(tmp_path):
    assert main(["--offline", FIXTURES, "--out", str(tmp_path), "--min-score", "10", "--quiet"]) == 0


def test_cli_include_exclude(tmp_path):
    out = str(tmp_path)
    main(["--offline", FIXTURES, "--out", out, "--include", "/product/", "--exclude", "scarf", "--format", "json", "--quiet"])
    urls = [p["url"] for p in json.load(open(os.path.join(out, "report.json")))["pages"]]
    assert urls and all("/product/" in u and "scarf" not in u for u in urls)


def test_cli_max_pages(tmp_path):
    main(["--offline", FIXTURES, "--out", str(tmp_path), "--max-pages", "2", "--format", "json", "--quiet"])
    assert json.load(open(os.path.join(str(tmp_path), "report.json")))["summary"]["pages_audited"] == 2


def test_cli_no_source_is_usage_error(capsys):
    assert main(["--quiet"]) == 2


def test_cli_bad_format(tmp_path):
    assert main(["--offline", FIXTURES, "--format", "pdf", "--out", str(tmp_path), "--quiet"]) == 2


def test_cli_missing_offline_path(tmp_path):
    assert main(["--offline", "/no/such/dir", "--out", str(tmp_path), "--quiet"]) == 2


def test_cli_list_checks(capsys):
    assert main(["--list-checks"]) == 0
    assert "PRICE_MISMATCH" in capsys.readouterr().out


def test_cli_type_override(tmp_path):
    main(["--offline", FIXTURES, "--out", str(tmp_path), "--type", "category", "--format", "json", "--quiet"])
    types = {p["type"] for p in json.load(open(os.path.join(str(tmp_path), "report.json")))["pages"]}
    assert types == {"category"}


def test_cli_urls_file_with_mocked_fetcher(tmp_path, monkeypatch):
    from ecom_seo_audit import cli
    from fakes import Clock, FakeResponse, FakeSession

    html = make_html(good_product())
    session = FakeSession({"https://shop.example.com/product/widget/": FakeResponse(200, html)})
    clock = Clock()
    real = cli.Fetcher

    def factory(**kw):
        return real(session=session, sleep=clock.sleep, clock=clock, **kw)

    monkeypatch.setattr(cli, "Fetcher", factory)
    urls = tmp_path / "urls.txt"
    urls.write_text("# comment\nhttps://shop.example.com/product/widget/\n")
    out = str(tmp_path / "o")
    assert main(["--urls", str(urls), "--out", out, "--no-cache", "--delay", "0", "--format", "json", "--quiet"]) == 0
    assert json.load(open(os.path.join(out, "report.json")))["summary"]["pages_audited"] == 1


def test_cli_sitemap_with_mocked_fetcher(tmp_path, monkeypatch):
    from ecom_seo_audit import cli
    from fakes import Clock, FakeResponse, FakeSession

    sitemap = ("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
               "<url><loc>https://shop.example.com/product/widget/</loc></url></urlset>")
    session = FakeSession({
        "https://shop.example.com/sitemap.xml": FakeResponse(200, sitemap, headers={"Content-Type": "application/xml"}),
        "https://shop.example.com/product/widget/": FakeResponse(200, make_html(good_product())),
        "https://shop.example.com/robots.txt": FakeResponse(200, "User-agent: *\nDisallow: /nothing\n"),
    })
    clock = Clock()
    real = cli.Fetcher
    monkeypatch.setattr(cli, "Fetcher", lambda **kw: real(session=session, sleep=clock.sleep, clock=clock, **kw))
    out = str(tmp_path / "o")
    assert main(["--sitemap", "https://shop.example.com/sitemap.xml", "--out", out, "--no-cache", "--delay", "0",
                 "--format", "json", "--quiet"]) == 0
    assert "https://shop.example.com/product/widget/" in session.calls


def test_cli_robots_blocked_pages_reported_skipped(tmp_path, monkeypatch):
    from ecom_seo_audit import cli
    from fakes import Clock, FakeResponse, FakeSession

    session = FakeSession({"https://shop.example.com/robots.txt": FakeResponse(200, "User-agent: *\nDisallow: /product/\n")})
    clock = Clock()
    real = cli.Fetcher
    monkeypatch.setattr(cli, "Fetcher", lambda **kw: real(session=session, sleep=clock.sleep, clock=clock, **kw))
    out = str(tmp_path / "o")
    main(["https://shop.example.com/product/widget/", "--out", out, "--no-cache", "--delay", "0", "--format", "json", "--quiet"])
    data = json.load(open(os.path.join(out, "report.json")))
    assert data["summary"]["pages_skipped"] == 1 and data["skipped"][0]["reason"] == "blocked by robots.txt"


def test_audit_config_defaults():
    cfg = AuditConfig()
    assert cfg.min_description_words == 50 and cfg.current_date()
