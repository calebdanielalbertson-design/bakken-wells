"""Fetch the public ND DMR GIS archives that the well pages are built from.

    python scripts/wells/fetch_dmr.py --out work/dmr            # download every source in sources.json
    python scripts/wells/fetch_dmr.py --out work/dmr --only wells
    python scripts/wells/fetch_dmr.py --list                     # print every .zip the DMR download page links to

This is the only step that needs network access; build_well_pages.py works from the files it
writes. Downloads stream to a .part file, are hashed while streaming, and are renamed only when
complete, so an interrupted run never leaves a truncated archive behind. A provenance record
(URL, bytes, SHA-256, server Last-Modified, retrieval time) is written beside the archives.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / 'sources.json'
USER_AGENT = 'bakkensubsurface-well-pages/1.0 (+https://github.com/calebdanielalbertson-design/bakken-wells)'


class FetchError(RuntimeError):
    pass


def load_sources(path: Path = SOURCES) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self._href, self._text = dict(attrs).get('href'), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self._href is not None:
            self.links.append((self._href, ' '.join(''.join(self._text).split())))
            self._href = None


def _open(url: str, timeout: float):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def list_archives(page_url: str, timeout: float = 60) -> list:
    """Return [(absolute_url, link_text)] for every .zip linked from the download page."""
    with _open(page_url, timeout) as response:
        html = response.read().decode(response.headers.get_content_charset() or 'utf-8', 'replace')
    parser = _Links()
    parser.feed(html)
    seen, archives = set(), []
    for href, text in parser.links:
        if not href or not href.lower().split('?')[0].endswith('.zip'):
            continue
        absolute = urllib.parse.urljoin(page_url, href)
        if absolute not in seen:
            seen.add(absolute)
            archives.append((absolute, text))
    return archives


def download(url: str, dest: Path, *, timeout: float = 300, attempts: int = 4, log=print) -> dict:
    """Stream ``url`` to ``dest`` with retries; return a provenance record."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + '.part')
    delay = 2.0
    for attempt in range(1, attempts + 1):
        digest, size = hashlib.sha256(), 0
        try:
            with _open(url, timeout) as response, part.open('wb') as out:
                last_modified = response.headers.get('Last-Modified')
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if size == 0:
                raise FetchError('empty response')
            part.replace(dest)
            return {'url': url, 'file': dest.name, 'bytes': size, 'sha256': digest.hexdigest(),
                    'lastModified': last_modified,
                    'retrievedAt': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')}
        except urllib.error.HTTPError as error:
            if error.code in (400, 401, 403, 404, 410):
                raise FetchError(f'{url}: HTTP {error.code}') from None
            problem = f'HTTP {error.code}'
        except (urllib.error.URLError, TimeoutError, OSError, FetchError) as error:
            problem = str(error)
        part.unlink(missing_ok=True)
        if attempt == attempts:
            raise FetchError(f'{url}: giving up after {attempts} attempts ({problem})')
        log(f'  retry {attempt}/{attempts - 1} for {dest.name} in {delay:.0f}s: {problem}')
        time.sleep(delay)
        delay *= 2
    raise FetchError(url)  # unreachable


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, help='folder for the downloaded archives (default work/dmr)')
    parser.add_argument('--only', action='append', help='source name(s) to fetch; default all with a URL')
    parser.add_argument('--list', action='store_true', help='print the archives linked from the download page and exit')
    parser.add_argument('--reuse', action='store_true', help='keep an archive that is already present instead of downloading again')
    parser.add_argument('--sources', type=Path, default=SOURCES)
    parser.add_argument('--timeout', type=float, default=300)
    args = parser.parse_args(argv)
    config = load_sources(args.sources)

    if args.list:
        archives = list_archives(config['downloadPage'])
        print(f'{len(archives)} archives linked from {config["downloadPage"]}:')
        for url, text in archives:
            print(f'  {url}' + (f'    ({text})' if text else ''))
        return 0

    out = args.out or Path('work/dmr')
    out.mkdir(parents=True, exist_ok=True)
    records, failed = [], []
    for source in config['sources']:
        if args.only and source['name'] not in args.only:
            continue
        if not source.get('url'):
            print(f'- skip  {source["name"]}: no URL configured ({"required" if source.get("required") else "optional"})')
            if source.get('required'):
                failed.append(source['name'])
            continue
        dest = out / (source.get('file') or Path(urllib.parse.urlsplit(source['url']).path).name)
        try:
            if args.reuse and dest.exists():
                record = {'url': source['url'], 'file': dest.name, 'bytes': dest.stat().st_size,
                          'sha256': sha256_file(dest), 'lastModified': None, 'retrievedAt': None, 'reused': True}
                print(f'- reuse {source["name"]}: {dest} ({record["bytes"]:,} bytes)')
            else:
                print(f'- fetch {source["name"]}: {source["url"]}')
                record = download(source['url'], dest, timeout=args.timeout)
                print(f'        {record["bytes"]:,} bytes, sha256 {record["sha256"][:16]}…')
            record.update({'name': source['name'], 'kind': source.get('kind', source['name'])})
            records.append(record)
        except FetchError as error:
            print(f'- FAIL  {source["name"]}: {error}')
            if source.get('required'):
                failed.append(source['name'])
    provenance = {'downloadPage': config['downloadPage'], 'attribution': config.get('attribution'),
                  'retrievedOn': datetime.now(timezone.utc).date().isoformat(), 'archives': records}
    (out / 'download-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    print(f'provenance: {out / "download-provenance.json"}')
    if failed:
        print(f'required sources failed: {", ".join(failed)}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
