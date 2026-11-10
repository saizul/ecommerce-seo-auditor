# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `--format markdown` writes `report.md`, a compact summary (score, severity counts, top fixes, lowest-scoring pages) sized for a GitHub issue or PR comment. Untrusted URLs and titles are escaped.

## [0.1.0] - 2026-11-09

### Added
- Command line tool `ecom-seo-audit` with sitemap (including indexes and gzip), URL-list and offline sources.
- Polite fetcher: robots.txt (with `Crawl-delay`), per-host delay, bounded concurrency, retry with `Retry-After`, on-disk cache.
- Product structured-data checks for JSON-LD and microdata, including `@graph`/`@id` resolution, `ProductGroup` variants and `AggregateOffer`.
- GTIN check-digit validation, rating and review consistency checks.
- Price-on-page vs schema price and availability vs on-page stock comparisons.
- Canonical checks for parameter/faceted URLs and paginated pages.
- Duplicate title and meta description detection, thin description, image alt coverage, breadcrumb and hreflang checks.
- Noindex detection via meta robots and `X-Robots-Tag`.
- HTML, CSV and JSON reports with a 0-100 score, grade and prioritised fixes.
- Test suite with HTML fixtures, ruff configuration and GitHub Actions CI for Python 3.10-3.13.
