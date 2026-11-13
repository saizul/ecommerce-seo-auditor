"""Compare an audit with a previous JSON report and gate CI on severity.

A baseline is any ``report.json`` written by this tool. Issues are matched by
``(url, code)``, so an issue whose detail text changes (for example a different
price) is still treated as the same known issue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .issues import SEVERITY_ORDER, Issue

Key = Tuple[str, str]


class BaselineError(ValueError):
    """The baseline file is missing, unreadable or not an ecom-seo-audit JSON report."""


@dataclass
class BaselineEntry:
    url: str
    code: str
    severity: str
    title: str


@dataclass
class Baseline:
    score: Optional[float]
    generated_at: str
    entries: Dict[Key, BaselineEntry]
    audited_urls: Set[str]


@dataclass
class BaselineDiff:
    score_before: Optional[float]
    score_after: float
    baseline_generated_at: str
    new_issues: List[Issue] = field(default_factory=list)
    resolved: List[BaselineEntry] = field(default_factory=list)
    unchanged: int = 0
    not_reaudited: int = 0

    @property
    def score_delta(self) -> Optional[float]:
        if self.score_before is None:
            return None
        return round(self.score_after - self.score_before, 1)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "baseline_generated_at": self.baseline_generated_at,
            "score_before": self.score_before,
            "score_after": self.score_after,
            "score_delta": self.score_delta,
            "new_count": len(self.new_issues),
            "resolved_count": len(self.resolved),
            "unchanged_count": self.unchanged,
            "not_reaudited_count": self.not_reaudited,
            "new": [i.as_dict() for i in self.new_issues],
            "resolved": [
                {"url": e.url, "code": e.code, "severity": e.severity, "title": e.title} for e in self.resolved
            ],
        }


def _severity_rank(severity: str) -> int:
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return len(SEVERITY_ORDER)


def parse_baseline(data: Any) -> Baseline:
    if not isinstance(data, dict) or data.get("tool") != "ecom-seo-audit":
        raise BaselineError("baseline is not an ecom-seo-audit JSON report")
    pages = data.get("pages")
    if not isinstance(pages, list):
        raise BaselineError("baseline report has no 'pages' list")
    entries: Dict[Key, BaselineEntry] = {}
    audited: Set[str] = set()
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("url"), str):
            raise BaselineError("baseline page entry without a url")
        url = page["url"]
        audited.add(url)
        issues = page.get("issues") or []
        if not isinstance(issues, list):
            raise BaselineError("baseline issues for %s are not a list" % url)
        for issue in issues:
            if not isinstance(issue, dict) or not isinstance(issue.get("code"), str):
                raise BaselineError("baseline issue for %s has no code" % url)
            key = (url, issue["code"])
            entries.setdefault(
                key,
                BaselineEntry(
                    url=url,
                    code=issue["code"],
                    severity=str(issue.get("severity", "")),
                    title=str(issue.get("title", "")),
                ),
            )
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    score = summary.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        score = None
    return Baseline(
        score=float(score) if score is not None else None,
        generated_at=str(data.get("generated_at", "")),
        entries=entries,
        audited_urls=audited,
    )


def load_baseline(path: str) -> Baseline:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise BaselineError("baseline file not found: %s" % path) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaselineError("cannot read baseline %s: %s" % (path, exc)) from exc
    return parse_baseline(data)


def compare(result, baseline: Baseline) -> BaselineDiff:
    """Diff an ``AuditResult`` against a baseline.

    Baseline issues on URLs that were not audited this time (filtered out, capped by
    ``--max-pages`` or skipped by robots.txt) are counted as ``not_reaudited`` instead of
    being reported as resolved.
    """
    current_urls = {p.url for p in result.pages}
    diff = BaselineDiff(
        score_before=baseline.score,
        score_after=result.score,
        baseline_generated_at=baseline.generated_at,
    )
    seen: Set[Key] = set()
    for page in result.pages:
        for issue in page.issues:
            key = (page.url, issue.code)
            seen.add(key)
            if key in baseline.entries:
                diff.unchanged += 1
            else:
                diff.new_issues.append(issue)
    for key, entry in baseline.entries.items():
        if key in seen:
            continue
        if entry.url in current_urls:
            diff.resolved.append(entry)
        else:
            diff.not_reaudited += 1
    diff.new_issues.sort(key=lambda i: (_severity_rank(i.severity), i.code, i.url))
    diff.resolved.sort(key=lambda e: (_severity_rank(e.severity), e.code, e.url))
    return diff


def issues_at_or_above(issues: Iterable[Issue], severity: str) -> List[Issue]:
    """Issues whose severity is ``severity`` or more severe."""
    if severity not in SEVERITY_ORDER:
        raise ValueError("unknown severity: %s" % severity)
    limit = SEVERITY_ORDER.index(severity)
    return [i for i in issues if _severity_rank(i.severity) <= limit]
