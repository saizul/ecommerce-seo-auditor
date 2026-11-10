"""Command line interface."""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from typing import List, Optional

from rich.console import Console
from rich.table import Table

from . import __version__
from .audit import run_audit
from .checks import AuditConfig
from .extract import Page, parse_page
from .fetch import DEFAULT_USER_AGENT, Fetcher
from .issues import CATALOG, SEVERITY_ORDER
from .offline import load_offline
from .report import FORMATS, write_reports
from .sitemap import SitemapError, load_sitemap_urls


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ecom-seo-audit",
        description="Audit product and category pages of WooCommerce, Shopify and other stores.",
    )
    src = p.add_argument_group("sources (use one or combine URLs/--urls)")
    src.add_argument("urls", nargs="*", help="page URLs to audit")
    src.add_argument("--sitemap", help="sitemap or sitemap index URL")
    src.add_argument("--sitemap-match", help="regex; follow only child sitemaps whose URL matches")
    src.add_argument("--urls", dest="urls_file", help="text file with one URL per line")
    src.add_argument("--offline", help="directory or file of saved HTML (no network access)")
    flt = p.add_argument_group("filtering")
    flt.add_argument("--include", help="regex; keep only matching URLs")
    flt.add_argument("--exclude", help="regex; drop matching URLs")
    flt.add_argument("--max-pages", type=int, default=200, help="maximum pages to audit (default 200)")
    flt.add_argument("--type", choices=["auto", "product", "category"], default="auto",
                     help="force the page type instead of auto-detecting")
    crawl = p.add_argument_group("polite crawling")
    crawl.add_argument("--delay", type=float, default=1.0, help="seconds between requests per host (default 1)")
    crawl.add_argument("--concurrency", type=int, default=2, help="parallel workers (default 2)")
    crawl.add_argument("--timeout", type=float, default=20.0)
    crawl.add_argument("--cache-dir", default=".seo-cache", help="response cache directory")
    crawl.add_argument("--cache-ttl", type=float, default=86400, help="cache lifetime in seconds")
    crawl.add_argument("--no-cache", action="store_true", help="disable the response cache")
    crawl.add_argument("--no-robots", action="store_true",
                       help="ignore robots.txt (only for sites you own or have permission to crawl)")
    crawl.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    out = p.add_argument_group("output")
    out.add_argument("--out", default="audit-report", help="report directory (default audit-report)")
    out.add_argument("--format", default="html,csv,json", help="comma list of html,csv,json,markdown")
    out.add_argument("--min-score", type=float, default=None, help="exit with status 1 if the site score is lower")
    out.add_argument("--quiet", action="store_true")
    thr = p.add_argument_group("thresholds")
    thr.add_argument("--min-description-words", type=int, default=50)
    thr.add_argument("--alt-threshold", type=float, default=0.8, help="minimum alt-text coverage (0-1)")
    thr.add_argument("--title-max", type=int, default=60)
    p.add_argument("--list-checks", action="store_true", help="print every check code and exit")
    p.add_argument("--version", action="version", version="ecom-seo-audit " + __version__)
    return p


def _read_url_file(path: str) -> List[str]:
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")]


def _filter_urls(urls: List[str], include: Optional[str], exclude: Optional[str], limit: int) -> List[str]:
    inc = re.compile(include) if include else None
    exc = re.compile(exclude) if exclude else None
    seen, out = set(), []
    for u in urls:
        if u in seen or (inc and not inc.search(u)) or (exc and exc.search(u)):
            continue
        seen.add(u)
        out.append(u)
    return out[:limit] if limit else out


def print_checks(console: Console) -> None:
    table = Table(title="Checks")
    for col in ("Code", "Severity", "Category", "Description"):
        table.add_column(col)
    for d in sorted(CATALOG.values(), key=lambda d: (SEVERITY_ORDER.index(d.severity), d.code)):
        table.add_row(d.code, d.severity, d.category, d.title)
    console.print(table)


def collect_pages(args, console: Console) -> List[Page]:
    if args.offline:
        entries = load_offline(args.offline)
        entries = [(u, h) for u, h in entries if _passes(u, args)]
        entries = entries[: args.max_pages] if args.max_pages else entries
        return [parse_page(u, h, page_type=args.type) for u, h in entries]

    fetcher = Fetcher(
        user_agent=args.user_agent,
        delay=args.delay,
        concurrency=args.concurrency,
        timeout=args.timeout,
        cache_dir=None if args.no_cache else args.cache_dir,
        cache_ttl=args.cache_ttl,
        respect_robots=not args.no_robots,
    )
    urls = list(args.urls)
    if args.urls_file:
        urls += _read_url_file(args.urls_file)
    if args.sitemap:
        urls += load_sitemap_urls(
            fetcher.fetch_bytes, args.sitemap, max_urls=args.max_pages * 3 or None,
            child_pattern=args.sitemap_match,
        )
    urls = _filter_urls(urls, args.include, args.exclude, args.max_pages)
    if not urls:
        raise SystemExit("no URLs to audit (check --include/--exclude)")
    console.print(
        "[dim]Fetching %d page(s), delay %.1fs, concurrency %d[/dim]" % (len(urls), args.delay, args.concurrency)
    )
    pages: List[Page] = []
    for res in fetcher.fetch_many(urls):
        if res.blocked or res.error:
            pages.append(
                Page(url=res.url, request_url=res.url, status=res.status, error=res.error, blocked=res.blocked)
            )
        else:
            pages.append(parse_page(res.final_url or res.url, res.text, res.status, res.headers,
                                    request_url=res.url, page_type=args.type))
    return pages


def _passes(url: str, args) -> bool:
    if args.include and not re.search(args.include, url):
        return False
    if args.exclude and re.search(args.exclude, url):
        return False
    return True


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console(stderr=True, quiet=args.quiet)
    if args.list_checks:
        print_checks(Console())
        return 0
    if not (args.urls or args.urls_file or args.sitemap or args.offline):
        console.print("[red]Provide URLs, --urls, --sitemap or --offline.[/red]")
        return 2
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    if not formats or any(f not in FORMATS for f in formats):
        console.print("[red]--format must be a comma list of html, csv, json, markdown[/red]")
        return 2
    try:
        pages = collect_pages(args, console)
    except (SitemapError, FileNotFoundError) as exc:
        console.print("[red]Error:[/red] %s" % exc)
        return 2

    cfg = AuditConfig(
        min_description_words=args.min_description_words,
        alt_threshold=args.alt_threshold,
        title_max=args.title_max,
        today=date.today(),
        page_type=args.type,
    )
    result = run_audit(pages, cfg)
    written = write_reports(result, args.out, formats)

    console.print("\n[bold]Score: %s (%s)[/bold]  pages: %d  skipped: %d" % (
        result.score, result.grade, result.total_pages, len(result.skipped)))
    table = Table(title="Top fixes")
    for col in ("Severity", "Issue", "Pages", "Gain"):
        table.add_column(col)
    for f in result.fixes[:10]:
        table.add_row(f.severity, "%s (%s)" % (f.title, f.code), str(f.pages_affected), "+%s" % f.score_gain)
    console.print(table)
    for path in written:
        console.print("wrote %s" % path)

    if args.min_score is not None and result.score < args.min_score:
        console.print("[red]Score %s is below --min-score %s[/red]" % (result.score, args.min_score))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
