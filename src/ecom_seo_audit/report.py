"""HTML, CSV and JSON report writers."""
from __future__ import annotations

import csv
import io
import json
import os
from typing import Any, Dict, List

from jinja2 import Environment, select_autoescape

from . import __version__
from .audit import AuditResult

CSV_FIELDS = ["url", "page_type", "page_score", "severity", "code", "category", "title", "detail", "fix"]

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>E-commerce SEO audit</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1c2330}
main{max-width:1100px;margin:0 auto;padding:24px}
h1{margin:0 0 4px}h2{margin-top:36px}
.muted{color:#667085}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}
.card{background:#fff;border-radius:10px;padding:14px 18px;min-width:130px;box-shadow:0 1px 2px rgba(0,0,0,.08)}
.card b{display:block;font-size:26px}
table{border-collapse:collapse;width:100%;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 2px rgba(0,0,0,.08)}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #eceef2;vertical-align:top}
th{background:#eef0f4;font-size:13px}
.sev{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600;color:#fff}
.sev-critical{background:#b42318}.sev-high{background:#d9480f}.sev-medium{background:#b7791f}
.sev-low{background:#2b6cb0}.sev-info{background:#667085}
.score-A,.score-B{color:#067647}.score-C{color:#b7791f}.score-D,.score-F{color:#b42318}
details{background:#fff;border-radius:10px;margin:8px 0;padding:8px 14px;box-shadow:0 1px 2px rgba(0,0,0,.08)}
summary{cursor:pointer;font-weight:600}
code{background:#eef0f4;padding:1px 5px;border-radius:4px;font-size:13px}
.url{word-break:break-all}
</style></head><body><main>
<h1>E-commerce SEO audit</h1>
<p class="muted">Generated {{ r.generated_at }} by ecom-seo-audit {{ version }}</p>
<div class="cards">
 <div class="card"><b class="score-{{ r.grade }}">{{ r.score }}</b>Site score ({{ r.grade }})</div>
 <div class="card"><b>{{ r.total_pages }}</b>Pages audited</div>
 {% for sev in severities %}<div class="card"><b>{{ r.severity_counts[sev] }}</b>{{ sev|capitalize }}</div>{% endfor %}
 {% if r.skipped %}<div class="card"><b>{{ r.skipped|length }}</b>Skipped (robots)</div>{% endif %}
</div>

<h2>Prioritised fixes</h2>
{% if r.fixes %}
<table><tr><th>#</th><th>Severity</th><th>Issue</th><th>Pages</th><th>Est. score gain</th><th>How to fix</th></tr>
{% for f in r.fixes %}<tr>
<td>{{ loop.index }}</td>
<td><span class="sev sev-{{ f.severity }}">{{ f.severity }}</span></td>
<td><b>{{ f.title }}</b><br><code>{{ f.code }}</code> <span class="muted">{{ f.category }}</span></td>
<td>{{ f.pages_affected }}</td><td>+{{ f.score_gain }}</td>
<td>{{ f.fix }}<br><span class="muted url">e.g. {{ f.examples[0] }}</span></td>
</tr>{% endfor %}</table>
{% else %}<p>No issues found.</p>{% endif %}

<h2>Pages</h2>
{% for p in r.pages %}
<details{% if p.issues and p.score < 70 %} open{% endif %}>
<summary><span class="score-{{ grade(p.score) }}">{{ p.score|int }}</span> &middot; {{ p.page_type }} &middot; <span class="url">{{ p.url }}</span> <span class="muted">({{ p.issues|length }} issues)</span></summary>
{% if p.issues %}<table>{% for i in p.issues %}<tr>
<td><span class="sev sev-{{ i.severity }}">{{ i.severity }}</span></td>
<td><code>{{ i.code }}</code></td><td>{{ i.title }}{% if i.detail %}<br><span class="muted">{{ i.detail }}</span>{% endif %}</td>
</tr>{% endfor %}</table>{% else %}<p>No issues.</p>{% endif %}
</details>
{% endfor %}

{% if r.skipped %}<h2>Skipped</h2><ul>{% for p in r.skipped %}<li class="url">{{ p.url }} &mdash; {{ p.note }}</li>{% endfor %}</ul>{% endif %}
</main></body></html>
"""


def _grade(score) -> str:
    from .audit import grade_for
    return grade_for(score or 0)


def to_dict(result: AuditResult) -> Dict[str, Any]:
    return {
        "tool": "ecom-seo-audit",
        "version": __version__,
        "generated_at": result.generated_at,
        "summary": {
            "score": result.score,
            "grade": result.grade,
            "pages_audited": result.total_pages,
            "pages_skipped": len(result.skipped),
            "severity_counts": result.severity_counts,
            "category_counts": result.category_counts,
        },
        "fixes": [
            {
                "rank": idx + 1,
                "code": f.code,
                "severity": f.severity,
                "category": f.category,
                "title": f.title,
                "fix": f.fix,
                "pages_affected": f.pages_affected,
                "estimated_score_gain": f.score_gain,
                "examples": f.examples,
            }
            for idx, f in enumerate(result.fixes)
        ],
        "pages": [
            {
                "url": p.url,
                "type": p.page_type,
                "status": p.status,
                "score": p.score,
                "issues": [i.as_dict() for i in p.issues],
            }
            for p in result.pages
        ],
        "skipped": [{"url": p.url, "reason": p.note} for p in result.skipped],
    }


def to_json(result: AuditResult) -> str:
    return json.dumps(to_dict(result), indent=2, ensure_ascii=False)


def _safe_cell(value: Any) -> Any:
    """Neutralise spreadsheet formula injection in untrusted text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def to_csv(result: AuditResult) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for p in result.pages:
        for i in p.issues:
            writer.writerow({
                k: _safe_cell(v)
                for k, v in {
                    "url": p.url,
                    "page_type": p.page_type,
                    "page_score": p.score,
                    "severity": i.severity,
                    "code": i.code,
                    "category": i.category,
                    "title": i.title,
                    "detail": i.detail,
                    "fix": i.fix,
                }.items()
            })
    return buf.getvalue()


def to_html(result: AuditResult) -> str:
    env = Environment(autoescape=select_autoescape(default=True, default_for_string=True))
    template = env.from_string(HTML_TEMPLATE)
    return template.render(
        r=result,
        version=__version__,
        severities=["critical", "high", "medium", "low"],
        grade=_grade,
    )


def write_reports(result: AuditResult, out_dir: str, formats: List[str]) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    written = []
    renderers = {"html": to_html, "csv": to_csv, "json": to_json}
    for fmt in formats:
        path = os.path.join(out_dir, "report." + fmt)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(renderers[fmt](result))
        written.append(path)
    return written
