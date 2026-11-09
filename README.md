# ecommerce-seo-auditor

A command-line auditor for the pages that make or lose money in an online store: **product pages and category pages**. Point it at a sitemap, a list of URLs, or a folder of saved HTML, and it produces an HTML, CSV and JSON report with a 0-100 score and a prioritised fix list.

It understands the markup that WooCommerce, Shopify and custom stores actually emit (JSON-LD `@graph`, `@id` references, microdata, `ProductGroup` variants, `AggregateOffer`), and it cross-checks structured data against what the shopper sees on the page.

Built by [Saizul Amin](https://saizul.com), e-commerce web developer and SEO specialist.

## Features

- **Product structured data** (JSON-LD and microdata): name, image, `offers.price`, `priceCurrency`, `availability`, `priceValidUntil`, brand, `sku`/`gtin`/`mpn`, GTIN check-digit validation, shipping/return policy hints.
- **`aggregateRating` / `review` consistency**: valid rating range, count present, review count vs rating count, markup vs visible review count.
- **Price on page vs schema price**: locale-aware number parsing (`1,299.00`, `1.299,00`, `Tk 1 299`), ignores struck-through regular prices and related-product widgets.
- **Out-of-stock handling**: availability markup vs on-page stock message / classes / disabled buttons.
- **Canonical problems** on parameter and faceted URLs (`?orderby=`, `?filter_color=`, `utm_*`, `?pa_size=`...), offsite/relative/multiple canonicals, canonical-not-self for sitemap URLs.
- **Pagination**: page 2+ canonicalised to page 1.
- **Duplicate titles and meta descriptions** across the audited set (canonicalised and noindexed pages are excluded).
- **Thin product descriptions**, **image alt coverage**, **BreadcrumbList** markup (JSON-LD and microdata), **hreflang** (codes, absolute URLs, self-reference, duplicates, return links), **noindex** on products and categories (meta robots and `X-Robots-Tag`).
- **Polite crawling**: robots.txt is respected (including `Crawl-delay`), per-host delay, bounded concurrency, retry with `Retry-After`, on-disk response cache.
- **Offline mode**: audit saved HTML with no network access at all.

60 checks in total; run `ecom-seo-audit --list-checks` for the full table.

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/saizul/ecommerce-seo-auditor.git
cd ecommerce-seo-auditor
python -m venv .venv && source .venv/bin/activate
pip install .
```

## Usage

```bash
# Audit a product sitemap (follows sitemap indexes; --sitemap-match limits child sitemaps)
ecom-seo-audit --sitemap https://shop.example.com/sitemap_index.xml --sitemap-match product --max-pages 100

# Audit specific URLs or a file of URLs
ecom-seo-audit https://shop.example.com/product/linen-shirt/ https://shop.example.com/product-category/shirts/
ecom-seo-audit --urls urls.txt --include '/products/' --exclude '\?'

# Audit saved HTML offline (try the bundled demo)
ecom-seo-audit --offline examples/demo-site --out audit-report

# Gate a CI job on the score
ecom-seo-audit --urls urls.txt --min-score 80
```

Reports are written to `--out` (default `audit-report/`): `report.html`, `report.csv`, `report.json`. A pre-generated example lives in [`examples/sample-report`](examples/sample-report).

### Options

| Option | Purpose |
| --- | --- |
| `--sitemap URL`, `--sitemap-match REGEX` | Read page URLs from a sitemap or index; filter child sitemaps |
| `--urls FILE`, positional URLs | Explicit URL lists (`#` comments allowed) |
| `--offline PATH` | Directory/file of saved HTML |
| `--include`, `--exclude`, `--max-pages` | Filter and cap the URL set (default cap 200) |
| `--type auto\|product\|category` | Force page type instead of detecting it |
| `--delay`, `--concurrency`, `--timeout` | Politeness controls (defaults: 1 s per host, 2 workers, 20 s) |
| `--cache-dir`, `--cache-ttl`, `--no-cache` | Response cache (default `.seo-cache`, 24 h) |
| `--no-robots` | Ignore robots.txt. Only for sites you own or have permission to crawl |
| `--format html,csv,json`, `--out DIR` | Report formats and directory |
| `--min-score N` | Exit with status 1 when the site score is below N |
| `--min-description-words`, `--alt-threshold`, `--title-max` | Tune thresholds |

Exit codes: `0` success, `1` score below `--min-score`, `2` usage or input error.

### Offline mode and URLs

Each saved file needs a source URL so canonical and parameter checks make sense. The URL is taken from, in order: `manifest.json` (`{"file.html": "https://..."}`) in the folder, a leading `<!-- url: https://... -->` comment, a browser "saved from url" comment, or a placeholder host.

## How scoring works

Each page starts at 100 and loses points once per distinct issue: critical 25, high 12, medium 6, low 2, info 0 (never below 0). The site score is the mean page score, graded A (90+), B (80+), C (70+), D (60+), F. Pages blocked by robots.txt are listed as skipped and excluded.

The fix list groups issues by code and ranks them by `severity weight x pages affected`; each fix shows the estimated site-score gain if it were resolved everywhere.

## Polite crawling

- robots.txt is fetched once per origin and honoured for the `ecom-seo-audit` user agent. A 404 means "allow all"; 401/403/5xx or an unreachable robots.txt is treated as "disallow all", as search engines do.
- A minimum delay between requests per host (raised automatically to `Crawl-delay` when robots.txt asks for more).
- Bounded worker pool, retries on 429/502/503/504 honouring `Retry-After`, responses cached on disk so re-runs do not hit the store again.

## Notes and limitations

- Page content is analysed from the server-rendered HTML; prices or stock messages injected by JavaScript will not be seen (the tool reports `PRICE_NOT_FOUND_ON_PAGE` rather than guessing).
- Price and stock detection use class-name and text heuristics tuned for WooCommerce, Shopify and common themes; custom themes may need `--type` or may produce false positives. Related-product, footer, sidebar and menu blocks are excluded from the analysis.
- Google's structured-data requirements change. The required/recommended field list follows the product snippet and merchant listing documentation as of the last release; verify against the current official docs before treating a finding as a hard rule.
- The tool makes GET requests only and never submits forms.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Released under the [MIT License](LICENSE).
