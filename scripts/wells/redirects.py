"""Turn the GitHub Pages copy of the well index into pages that forward to bakkensubsurface.com/wells/.

    python scripts/wells/redirects.py --old work/wells-site --new work/wells-kv-site \
        --to https://bakkensubsurface.com/wells --old-base-path /bakken-wells --out work/wells-redirects \
        [--site-verification <token>]

Find a well moved from <owner>.github.io/bakken-wells/ to bakkensubsurface.com/wells/ on 2026-11-26. For every
page in the GitHub Pages build (--old, the default layout) this writes a small page at the same path that sends the
visitor, and search engines, to the same page at the new address: a meta refresh of 0 seconds, a canonical link, a
script that keeps the query and the #fragment, and a plain link for anyone with scripts and refresh turned off.

The two layouts differ in two ways: well pages sit under /wells/<slug>/ on GitHub Pages and at /wells/<slug>/ on
the site (one /wells/ less), and county and township addresses carry the state on the site (/counties/nd-mckenzie/,
/townships/nd-153n-91w/). Everything else keeps its path. When --new is given, every target is checked against
that build; a target it does not have falls back to the nearest page above it that it does have, so no old page
forwards to a 404. 404.html applies the same rule in the browser for addresses that never had a page.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

STATE_PREFIX = 'nd-'          # every page on the GitHub Pages site is a North Dakota page
PREFIXED = ('counties', 'townships')
TITLE = re.compile(r'<title>(.*?)</title>', re.S | re.I)


def new_parts(parts: list[str]) -> list[str]:
    """Path segments of an old page (without the GitHub Pages base path) -> segments under the new /wells/."""
    parts = [p for p in parts if p]
    if parts and parts[0] == 'wells':
        return parts[1:]
    if len(parts) == 2 and parts[0] in PREFIXED and not parts[1].startswith(STATE_PREFIX):
        return [parts[0], STATE_PREFIX + parts[1]]
    return parts


def target(parts: list[str], new_root: Path | None) -> list[str]:
    """new_parts, walked up to the nearest page the new build has (when it is given)."""
    mapped = new_parts(parts)
    if new_root is None:
        return mapped
    while mapped and not (new_root.joinpath(*mapped) / 'index.html').is_file():
        mapped = mapped[:-1]
    return mapped


def url_for(to: str, parts: list[str]) -> str:
    return to.rstrip('/') + '/' + ''.join(f'{p}/' for p in parts)


def old_title(page: Path) -> str | None:
    with page.open('r', encoding='utf-8', errors='replace') as handle:
        head = handle.read(4096)
    found = TITLE.search(head)
    return html.unescape(found.group(1).strip()) if found else None


def stub(url: str, title: str | None, verification: str | None = None) -> str:
    u = html.escape(url, quote=True)
    name = html.escape(title or 'Find a well')
    meta = f'<meta name="google-site-verification" content="{html.escape(verification, quote=True)}">' if verification else ''
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{name} (moved)</title><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<link rel="canonical" href="{u}"><meta http-equiv="refresh" content="0; url={u}">{meta}'
            f'<script>location.replace({json.dumps(url)}+location.search+location.hash)</script></head>'
            '<body style="font-family:system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem">'
            f'<p>Find a well has moved to Bakken Subsurface. This page is now at <a href="{u}">{u}</a>.</p></body></html>')


def not_found_page(to: str, old_base_path: str) -> str:
    """404.html: the same mapping in the browser, for addresses that never had a page here."""
    base = old_base_path.rstrip('/')
    script = ('(function(){var b=' + json.dumps(base) + ',p=location.pathname;'
              'if(b&&p.indexOf(b)===0)p=p.slice(b.length);'
              'var s=p.split("/").filter(Boolean);'
              'if(s[0]==="wells")s=s.slice(1);'
              'else if(s.length===2&&(s[0]==="counties"||s[0]==="townships")&&s[1].indexOf("nd-")!==0)s[1]="nd-"+s[1];'
              'location.replace(' + json.dumps(to.rstrip('/') + '/') + '+s.map(function(x){return x+"/"}).join("")+location.search+location.hash)})()')
    u = html.escape(to.rstrip('/') + '/', quote=True)
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Find a well (moved)</title>'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex"><script>{script}</script></head>'
            '<body style="font-family:system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem">'
            f'<p>Find a well has moved to Bakken Subsurface: <a href="{u}">{u}</a>.</p></body></html>')


def write_redirects(old: Path, out: Path, to: str, old_base_path: str = '', new: Path | None = None,
                    verification: str | None = None) -> dict:
    """Writes the forwarding site to out. Returns counts for the log."""
    counts = {'pages': 0, 'exact': 0, 'fellBack': 0}
    out.mkdir(parents=True, exist_ok=True)
    for page in sorted(old.rglob('index.html')):
        parts = list(page.relative_to(old).parent.parts)
        mapped = target(parts, new)
        exact = mapped == new_parts(parts)
        counts['pages'] += 1
        counts['exact' if exact else 'fellBack'] += 1
        dest = out.joinpath(*parts) / 'index.html'
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(stub(url_for(to, mapped), old_title(page), verification if not parts else None), encoding='utf-8')
    (out / '404.html').write_text(not_found_page(to, old_base_path), encoding='utf-8')
    (out / 'robots.txt').write_text('User-agent: *\nAllow: /\n', encoding='utf-8')   # crawlers must reach the pages to see where they went
    (out / '.nojekyll').write_text('', encoding='utf-8')
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--old', required=True, type=Path, help='the GitHub Pages build (default layout)')
    parser.add_argument('--new', type=Path, help='the build for the site (--layout site), to check every target')
    parser.add_argument('--to', default='https://bakkensubsurface.com/wells', help='the new base URL')
    parser.add_argument('--old-base-path', default='', help='the GitHub Pages path the old site is served under, e.g. /bakken-wells')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--site-verification', help='Google Search Console token to keep on the home page')
    args = parser.parse_args(argv)
    if not (args.old / 'index.html').is_file():
        print(f'error: no index.html in {args.old}', file=sys.stderr)
        return 1
    counts = write_redirects(args.old, args.out, args.to, args.old_base_path, args.new, (args.site_verification or '').strip() or None)
    print(f'{counts["pages"]:,} forwarding pages at {args.out} -> {args.to}: {counts["exact"]:,} to the same page, '
          f'{counts["fellBack"]:,} to the nearest page above it')
    return 0


if __name__ == '__main__':
    sys.exit(main())
