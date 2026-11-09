"""Run all checks over a set of pages and compute scores and prioritised fixes."""
from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .checks import AuditConfig, check_page, check_site
from .extract import Page
from .issues import SEVERITY_ORDER, SEVERITY_WEIGHTS, Issue


@dataclass
class PageResult:
    url: str
    page_type: str
    status: int
    score: Optional[float]
    issues: List[Issue] = field(default_factory=list)
    skipped: bool = False
    note: str = ""


@dataclass
class Fix:
    code: str
    severity: str
    category: str
    title: str
    fix: str
    pages_affected: int
    priority: float
    score_gain: float
    examples: List[str]


@dataclass
class AuditResult:
    pages: List[PageResult]
    skipped: List[PageResult]
    score: float
    grade: str
    severity_counts: Dict[str, int]
    fixes: List[Fix]
    generated_at: str
    category_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def total_pages(self) -> int:
        return len(self.pages)


def score_issues(issues: List[Issue]) -> float:
    penalty = sum(SEVERITY_WEIGHTS[i.severity] for i in issues)
    return float(max(0, 100 - penalty))


def grade_for(score: float) -> str:
    for threshold, letter in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
        if score >= threshold:
            return letter
    return "F"


def build_fixes(results: List[PageResult]) -> List[Fix]:
    total = max(1, len(results))
    grouped: OrderedDict[str, List[Issue]] = OrderedDict()
    for r in results:
        for issue in r.issues:
            grouped.setdefault(issue.code, []).append(issue)
    fixes = []
    for code, items in grouped.items():
        first = items[0]
        weight = SEVERITY_WEIGHTS[first.severity]
        affected = len({i.url for i in items})
        fixes.append(
            Fix(
                code=code,
                severity=first.severity,
                category=first.category,
                title=first.title,
                fix=first.fix,
                pages_affected=affected,
                priority=(weight if weight else 0.5) * affected,
                score_gain=round(weight * affected / total, 2),
                examples=[i.url for i in items[:3]],
            )
        )
    fixes.sort(key=lambda f: (-f.priority, SEVERITY_ORDER.index(f.severity), f.code))
    return fixes


def run_audit(pages: List[Page], cfg: Optional[AuditConfig] = None, now: Optional[datetime] = None) -> AuditResult:
    cfg = cfg or AuditConfig()
    scored: List[PageResult] = []
    skipped: List[PageResult] = []
    crawled = [p for p in pages if not p.blocked]
    site_issues = check_site(crawled)

    for page in pages:
        if page.blocked:
            note = page.error or "blocked by robots.txt"
            skipped.append(PageResult(page.url, page.page_type, 0, None, [], True, note))
            continue
        issues = check_page(page, cfg) + site_issues.get(page.url, [])
        unique: Dict[str, Issue] = OrderedDict()
        for i in issues:
            unique.setdefault(i.code, i)
        final = sorted(unique.values(), key=lambda i: SEVERITY_ORDER.index(i.severity))
        scored.append(PageResult(page.url, page.page_type, page.status, score_issues(final), final))

    overall = round(sum(r.score or 0 for r in scored) / len(scored), 1) if scored else 0.0
    counts = Counter(i.severity for r in scored for i in r.issues)
    cats = Counter(i.category for r in scored for i in r.issues)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return AuditResult(
        pages=scored,
        skipped=skipped,
        score=overall,
        grade=grade_for(overall),
        severity_counts={s: counts.get(s, 0) for s in SEVERITY_ORDER},
        fixes=build_fixes(scored),
        generated_at=stamp,
        category_counts=dict(cats),
    )
