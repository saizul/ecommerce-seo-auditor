import json

import pytest

from conftest import FIXTURES, URL, good_product, make_html
from ecom_seo_audit.audit import run_audit
from ecom_seo_audit.baseline import (
    BaselineError,
    compare,
    issues_at_or_above,
    load_baseline,
    parse_baseline,
)
from ecom_seo_audit.cli import main
from ecom_seo_audit.extract import parse_page
from ecom_seo_audit.issues import make_issue
from ecom_seo_audit.report import to_dict, to_html, to_markdown

OTHER = "https://shop.example.com/product/other/"


def _report(pages, score=80.0):
    return {
        "tool": "ecom-seo-audit",
        "generated_at": "2026-11-01T00:00:00Z",
        "summary": {"score": score},
        "pages": [
            {"url": url, "issues": [{"code": c, "severity": "medium", "title": c} for c in codes]}
            for url, codes in pages.items()
        ],
    }


@pytest.fixture
def audited(cfg):
    page = parse_page(URL, make_html(good_product(brand=None, sku=None), url=URL))
    res = run_audit([page], cfg)
    assert {i.code for i in res.pages[0].issues} >= {"BRAND_MISSING", "IDENTIFIER_MISSING"}
    return res


# ------------------------------------------------------------------ parsing
def test_parse_baseline_reads_entries_and_score():
    b = parse_baseline(_report({URL: ["BRAND_MISSING", "BRAND_MISSING", "TITLE_LONG"]}, score=71.5))
    assert b.score == 71.5 and b.generated_at == "2026-11-01T00:00:00Z"
    assert set(b.entries) == {(URL, "BRAND_MISSING"), (URL, "TITLE_LONG")}
    assert b.audited_urls == {URL}


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"tool": "something-else", "pages": []},
        {"tool": "ecom-seo-audit"},
        {"tool": "ecom-seo-audit", "pages": [{"issues": []}]},
        {"tool": "ecom-seo-audit", "pages": [{"url": URL, "issues": "nope"}]},
        {"tool": "ecom-seo-audit", "pages": [{"url": URL, "issues": [{"severity": "high"}]}]},
    ],
)
def test_parse_baseline_rejects_bad_reports(data):
    with pytest.raises(BaselineError):
        parse_baseline(data)


def test_parse_baseline_tolerates_missing_score():
    data = _report({URL: []})
    data["summary"] = {"score": "high"}
    assert parse_baseline(data).score is None


def test_load_baseline_errors(tmp_path):
    with pytest.raises(BaselineError, match="not found"):
        load_baseline(str(tmp_path / "missing.json"))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(BaselineError, match="cannot read"):
        load_baseline(str(bad))


# ------------------------------------------------------------------ diffing
def test_compare_classifies_new_resolved_unchanged(audited):
    baseline = parse_baseline(_report({URL: ["BRAND_MISSING", "TITLE_MISSING"]}, score=70.0))
    diff = compare(audited, baseline)
    new_codes = {i.code for i in diff.new_issues}
    assert "BRAND_MISSING" not in new_codes and "IDENTIFIER_MISSING" in new_codes
    assert [e.code for e in diff.resolved] == ["TITLE_MISSING"]
    assert diff.unchanged == 1 and diff.not_reaudited == 0
    assert diff.score_delta == round(audited.score - 70.0, 1)


def test_compare_ignores_pages_not_reaudited(audited):
    baseline = parse_baseline(_report({URL: [], OTHER: ["NOINDEX_PRODUCT", "TITLE_LONG"]}))
    diff = compare(audited, baseline)
    assert diff.resolved == [] and diff.not_reaudited == 2


def test_compare_new_issues_sorted_by_severity(audited):
    diff = compare(audited, parse_baseline(_report({})))
    order = ["critical", "high", "medium", "low", "info"]
    ranks = [order.index(i.severity) for i in diff.new_issues]
    assert ranks == sorted(ranks) and len(diff.new_issues) == len(audited.pages[0].issues)


def test_compare_identical_run_has_no_changes(audited):
    diff = compare(audited, parse_baseline(to_dict(audited)))
    assert diff.new_issues == [] and diff.resolved == []
    assert diff.unchanged == len(audited.pages[0].issues) and diff.score_delta == 0.0


def test_issues_at_or_above():
    issues = [make_issue(c, URL) for c in ("NOINDEX_PRODUCT", "GTIN_INVALID", "BRAND_MISSING", "TITLE_LONG")]
    assert [i.code for i in issues_at_or_above(issues, "high")] == ["NOINDEX_PRODUCT", "GTIN_INVALID"]
    assert len(issues_at_or_above(issues, "info")) == 4
    assert issues_at_or_above(issues[2:], "critical") == []
    with pytest.raises(ValueError):
        issues_at_or_above(issues, "severe")


# ------------------------------------------------------------------ reports
def test_reports_include_baseline_section(audited):
    audited.baseline = compare(audited, parse_baseline(_report({URL: ["BRAND_MISSING", "TITLE_MISSING"]})))
    data = to_dict(audited)["baseline"]
    assert data["resolved_count"] == 1 and data["unchanged_count"] == 1
    assert data["new_count"] == len(data["new"]) and data["resolved"][0]["code"] == "TITLE_MISSING"
    md = to_markdown(audited)
    assert "### Changes since baseline" in md and "`IDENTIFIER_MISSING`" in md
    assert "Changes since baseline" in to_html(audited)


def test_reports_without_baseline_unchanged(audited):
    assert "baseline" not in to_dict(audited)
    assert "Changes since baseline" not in to_markdown(audited)
    assert "Changes since baseline" not in to_html(audited)


def test_markdown_baseline_escapes_and_caps(audited):
    for issue in audited.pages[0].issues:
        issue.url = "https://a.com/x|<b>"
    audited.baseline = compare(audited, parse_baseline(_report({})))
    md = to_markdown(audited, max_pages=1)
    assert "x\\|\\<b\\>" in md and "more new issues in the full report" in md


# ------------------------------------------------------------------ CLI
def _run(tmp_path, *extra):
    return main(["--offline", FIXTURES, "--out", str(tmp_path), "--format", "json", "--quiet", *extra])


def test_cli_fail_on_without_baseline(tmp_path):
    assert _run(tmp_path, "--fail-on", "critical") == 1


def test_cli_fail_on_rejects_unknown_severity(tmp_path):
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, "--fail-on", "severe")
    assert exc.value.code == 2


def test_cli_baseline_same_run_passes_fail_on(tmp_path):
    first = tmp_path / "first"
    assert _run(first) == 0
    second = tmp_path / "second"
    code = _run(second, "--baseline", str(first / "report.json"), "--fail-on", "info")
    assert code == 0
    data = json.loads((second / "report.json").read_text(encoding="utf-8"))
    assert data["baseline"]["new_count"] == 0 and data["baseline"]["resolved_count"] == 0
    assert data["baseline"]["unchanged_count"] == sum(len(p["issues"]) for p in data["pages"])


def test_cli_baseline_detects_regression(tmp_path):
    first = tmp_path / "first"
    _run(first)
    report = json.loads((first / "report.json").read_text(encoding="utf-8"))
    page = next(p for p in report["pages"] if any(i["severity"] == "critical" for i in p["issues"]))
    page["issues"] = [i for i in page["issues"] if i["severity"] != "critical"]
    edited = tmp_path / "baseline.json"
    edited.write_text(json.dumps(report), encoding="utf-8")
    out = tmp_path / "second"
    assert _run(out, "--baseline", str(edited), "--fail-on", "critical") == 1
    assert _run(out, "--baseline", str(edited)) == 0
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert data["baseline"]["new_count"] >= 1
    assert all(i["url"] == page["url"] for i in data["baseline"]["new"])


def test_cli_baseline_errors_are_usage_errors(tmp_path):
    assert _run(tmp_path, "--baseline", str(tmp_path / "nope.json")) == 2
    other = tmp_path / "other.json"
    other.write_text('{"tool": "x"}', encoding="utf-8")
    assert _run(tmp_path, "--baseline", str(other)) == 2


def test_cli_min_score_and_fail_on_combine(tmp_path):
    first = tmp_path / "first"
    _run(first)
    assert _run(tmp_path / "b", "--baseline", str(first / "report.json"), "--fail-on", "info", "--min-score", "99") == 1
