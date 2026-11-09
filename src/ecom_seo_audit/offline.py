"""Offline mode: audit saved HTML files without any network access."""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Tuple

_URL_COMMENTS = [
    re.compile(r"<!--\s*saved from url=\(\d+\)(\S+)\s*-->", re.I),
    re.compile(r"<!--\s*url:\s*(\S+)\s*-->", re.I),
]
FALLBACK_HOST = "https://offline.invalid/"


def _url_for(rel_path: str, html: str, manifest: Dict[str, str]) -> str:
    if rel_path in manifest:
        return manifest[rel_path]
    head = html[:2000]
    for rx in _URL_COMMENTS:
        m = rx.search(head)
        if m:
            return m.group(1)
    return FALLBACK_HOST + rel_path.replace(os.sep, "/")


def load_offline(path: str) -> List[Tuple[str, str]]:
    """Return [(url, html)] from a single file or a directory of .html/.htm files.

    The source URL of each file is taken, in order, from `manifest.json`
    ({"file.html": "https://..."}), a leading `<!-- url: ... -->` or browser
    "saved from url" comment, or a placeholder host.
    """
    if os.path.isfile(path):
        files = [(os.path.basename(path), path)]
        base = os.path.dirname(path)
    elif os.path.isdir(path):
        base = path
        files = []
        for root, _dirs, names in os.walk(path):
            for name in sorted(names):
                if name.lower().endswith((".html", ".htm")):
                    full = os.path.join(root, name)
                    files.append((os.path.relpath(full, path), full))
        files.sort()
    else:
        raise FileNotFoundError(path)

    manifest: Dict[str, str] = {}
    manifest_path = os.path.join(base, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = {str(k): str(v) for k, v in json.load(fh).items()}

    out = []
    for rel, full in files:
        with open(full, encoding="utf-8", errors="replace") as fh:
            html = fh.read()
        out.append((_url_for(rel, html, manifest), html))
    return out
