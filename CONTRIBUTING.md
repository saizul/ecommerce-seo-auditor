# Contributing

Thanks for helping improve ecommerce-seo-auditor.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before opening a pull request

```bash
ruff check .
pytest
```

CI runs both on Python 3.10, 3.11, 3.12 and 3.13. Code should stay compatible with Python 3.10+ and avoid heavy dependencies.

## Adding a check

1. Add an entry to the catalog in `src/ecom_seo_audit/issues.py` (code, severity, category, title, fix text).
2. Implement the check in `src/ecom_seo_audit/checks.py` and call it from `check_page` (or `check_site` for cross-page checks).
3. If new page data is needed, extract it in `src/ecom_seo_audit/extract.py`.
4. Add tests: a focused unit test built with the helpers in `tests/conftest.py`, and a fixture under `tests/fixtures/site/` when real-world markup matters.

## Guidelines

- Use synthetic data in tests and fixtures (`example.com`, invented products). Never commit real store data or credentials.
- Tests must not touch the network; use `tests/fakes.py`.
- Keep the crawler polite: do not add features that bypass robots.txt by default or raise default request rates.
- Cite the relevant official documentation in the pull request when a check is based on a search-engine requirement.

## Reporting bugs

Open an issue with the URL pattern (or a minimal HTML sample), the command you ran and the output you expected.
