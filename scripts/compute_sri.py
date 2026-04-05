"""
Smart PUC — SRI Hash Injector
==============================

Scans every HTML file under ``frontend/``, finds ``<script>`` and
``<link>`` tags that load from the three whitelisted CDNs
(cdnjs.cloudflare.com, cdn.jsdelivr.net, unpkg.com), downloads each
referenced URL, computes a SHA-384 Subresource Integrity hash, and
rewrites the tag in place to include the ``integrity=`` and
``crossorigin="anonymous"`` attributes.

Why this script exists
----------------------
Real SRI hashes cannot be hand-written — they depend on the exact bytes
served by the CDN. Committing placeholder hashes would break the pages.
Instead, we ship this script; CI or developers run it once, the HTML
files are updated in place, and the diff is committed.

Usage
-----
::

    python scripts/compute_sri.py

Idempotent. Re-running does not change files whose hashes already match.

Requirements
------------
    pip install requests
"""

from __future__ import annotations

import base64
import hashlib
import re
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: 'requests' is required. pip install requests", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

ALLOWED_HOSTS = ("cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com")

TAG_RE = re.compile(
    r"""<(script|link)\b([^>]*?)\bsrc=(?P<q1>["'])(?P<src>https://[^"']+)(?P=q1)([^>]*)>""",
    re.IGNORECASE,
)
# <link> uses href= not src=. Handle separately.
LINK_RE = re.compile(
    r"""<link\b([^>]*?)\bhref=(?P<q1>["'])(?P<href>https://[^"']+)(?P=q1)([^>]*)>""",
    re.IGNORECASE,
)


def sri_for(url: str) -> str:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    digest = hashlib.sha384(r.content).digest()
    return "sha384-" + base64.b64encode(digest).decode("ascii")


def rewrite(html: str) -> tuple[str, int]:
    replacements = 0

    def repl_script(m: re.Match) -> str:
        nonlocal replacements
        tag_name = m.group(1)
        pre = m.group(2)
        quote = m.group("q1")
        src = m.group("src")
        post = m.group(5)
        if not any(host in src for host in ALLOWED_HOSTS):
            return m.group(0)
        # Keep an existing integrity hash if present.
        if re.search(r"integrity=", pre + post):
            return m.group(0)
        try:
            integrity = sri_for(src)
        except Exception as e:
            print(f"  [skip] {src}: {e}")
            return m.group(0)
        replacements += 1
        return (f'<{tag_name}{pre}src={quote}{src}{quote}'
                f' integrity="{integrity}" crossorigin="anonymous"{post}>')

    def repl_link(m: re.Match) -> str:
        nonlocal replacements
        pre = m.group(1)
        quote = m.group("q1")
        href = m.group("href")
        post = m.group(4)
        if not any(host in href for host in ALLOWED_HOSTS):
            return m.group(0)
        if re.search(r"integrity=", pre + post):
            return m.group(0)
        try:
            integrity = sri_for(href)
        except Exception as e:
            print(f"  [skip] {href}: {e}")
            return m.group(0)
        replacements += 1
        return (f'<link{pre}href={quote}{href}{quote}'
                f' integrity="{integrity}" crossorigin="anonymous"{post}>')

    html = TAG_RE.sub(repl_script, html)
    html = LINK_RE.sub(repl_link, html)
    return html, replacements


def main() -> int:
    if not FRONTEND.exists():
        print(f"frontend/ not found at {FRONTEND}", file=sys.stderr)
        return 2

    total = 0
    for html_file in sorted(FRONTEND.glob("*.html")):
        print(f"Scanning {html_file.name}")
        original = html_file.read_text(encoding="utf-8")
        rewritten, n = rewrite(original)
        if n > 0 and rewritten != original:
            html_file.write_text(rewritten, encoding="utf-8")
            print(f"  -> updated {n} tag(s)")
            total += n
        else:
            print("  -> no changes")
    print(f"\nDone. {total} tag(s) updated across the frontend.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
