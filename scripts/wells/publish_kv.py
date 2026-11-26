"""Publish a built well index (--layout site) to the Workers KV namespace that bakkensubsurface.com/wells/ reads.

    python scripts/wells/publish_kv.py --site work/wells-site --to-folder work/wells-kv        # local stand-in for KV
    python scripts/wells/publish_kv.py --site work/wells-site --kv                            # upload what changed
    python scripts/wells/publish_kv.py --site work/wells-site --kv --dry-run                  # say what would change

Every file of the build becomes one key: the URL path it is served at (a folder's index.html is keyed by the
folder with a trailing slash, so out/33-053-04567/index.html is "/wells/33-053-04567/"). The value is the file,
gzip-compressed; the metadata is {"t": content type, "h": first 16 hex of the sha256 of the file}. The Worker
(bakken-site worker/wells.js) serves the keys and adds the site's header and footer to HTML pages.

The upload sends only keys whose hash changed since the last publish and deletes keys the build no longer has.
The last publish is remembered in KV itself, as a gzipped manifest under MANIFEST_KEY, written after every
page has gone up, so a run that fails part-way sends the rest next time.

--kv needs CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN (Workers KV Storage write on that account) and
WELLS_KV_NAMESPACE_ID in the environment. Standard library only.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MANIFEST_KEY = '/wells/.manifest.json.gz'
TYPES = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
         '.json': 'application/json', '.xml': 'application/xml', '.txt': 'text/plain; charset=utf-8', '.svg': 'image/svg+xml'}
SKIP = {'.nojekyll', 'CNAME', 'robots.txt'}
API = 'https://api.cloudflare.com/client/v4'
BATCH_PAIRS = 5000            # the bulk API takes up to 10,000 pairs ...
BATCH_BYTES = 60_000_000      # ... and 100 MB per request; base64 adds a third
MAX_VALUE = 25 * 1024 * 1024  # KV's limit per value


class PublishError(RuntimeError):
    pass


def key_for(rel: str, mount: str) -> str:
    """'33-053-04567/index.html' -> '/wells/33-053-04567/'; 'index.html' -> '/wells/'; 'assets/well.css' -> '/wells/assets/well.css'."""
    rel = rel.replace('\\', '/')
    if rel == 'index.html':
        return mount + '/'
    if rel.endswith('/index.html'):
        return f'{mount}/{rel[:-len("index.html")]}'
    return f'{mount}/{rel}'


def collect(site: Path, mount: str | None = None):
    """{key: {'path', 't', 'h'}} for every file of the build, and the mount path ('/wells')."""
    site = Path(site)
    build = json.loads((site / 'build.json').read_text(encoding='utf-8'))
    if build.get('layout') != 'site':
        raise PublishError(f'{site} was built with layout {build.get("layout")!r}; publish only a --layout site build')
    mount = (mount or build.get('basePath') or '').rstrip('/')
    if not mount.startswith('/'):
        raise PublishError(f'mount path {mount!r} must start with "/" (the build.json basePath, e.g. /wells)')
    entries = {}
    for path in sorted(p for p in site.rglob('*') if p.is_file()):
        rel = path.relative_to(site).as_posix()
        if path.name in SKIP or any(part.startswith('.') for part in rel.split('/')):
            continue
        kind = TYPES.get(path.suffix.lower())
        if not kind:
            raise PublishError(f'{rel}: no content type for {path.suffix!r}')
        data = path.read_bytes()
        key = key_for(rel, mount)
        if len(key.encode('utf-8')) > 512:
            raise PublishError(f'{rel}: key longer than 512 bytes')
        entries[key] = {'path': path, 't': kind, 'h': hashlib.sha256(data).hexdigest()[:16]}
    if mount + '/' not in entries:
        raise PublishError(f'{site} has no index.html')
    return entries, mount


def packed(path: Path) -> bytes:
    data = gzip.compress(path.read_bytes(), compresslevel=9, mtime=0)
    if len(data) > MAX_VALUE:
        raise PublishError(f'{path}: {len(data):,} bytes gzipped, over the KV limit')
    return data


def manifest_of(entries):
    return {key: {'t': e['t'], 'h': e['h']} for key, e in entries.items()}


def plan(entries, previous):
    """(keys to write, keys to delete) against the last published manifest."""
    previous = previous or {}
    write = [k for k, e in entries.items() if (previous.get(k) or {}).get('h') != e['h'] or (previous.get(k) or {}).get('t') != e['t']]
    delete = sorted(k for k in previous if k not in entries)
    return sorted(write), delete


# ----------------------------------------------------------------------------- local folder


def to_folder(entries, out: Path):
    """Write the contract's local stand-in: keys.json plus values/<hash>.gz."""
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    (out / 'values').mkdir(parents=True)
    keys = {}
    for key, e in entries.items():
        name = f'{e["h"]}.gz'
        target = out / 'values' / name
        if not target.exists():
            target.write_bytes(packed(e['path']))
        keys[key] = {'f': name, 't': e['t'], 'h': e['h']}
    (out / 'keys.json').write_text(json.dumps(keys, indent=0, sort_keys=True) + '\n', encoding='utf-8')
    return len(keys)


# ----------------------------------------------------------------------------- Cloudflare


class KV:
    def __init__(self, account: str, namespace: str, token: str, *, retries=5, opener=None):
        self.base = f'{API}/accounts/{account}/storage/kv/namespaces/{namespace}'
        self.token, self.retries = token, retries
        self.open = opener or urllib.request.urlopen

    def call(self, method, path, body=None, *, raw=False):
        data = None if body is None else json.dumps(body, separators=(',', ':')).encode('utf-8')
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(self.base + path, data=data, method=method, headers={
                'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json', 'User-Agent': 'bakken-wells-publisher'})
            try:
                with self.open(request, timeout=120) as response:
                    payload = response.read()
                if raw:
                    return payload
                result = json.loads(payload or b'{}')
                if not result.get('success', True):
                    raise PublishError(f'{method} {path}: {result.get("errors")}')
                return result
            except urllib.error.HTTPError as error:
                if raw and error.code == 404:
                    return None
                if error.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(min(60, 2 ** attempt * 2))
                    continue
                detail = error.read()[:500].decode('utf-8', 'replace')
                raise PublishError(f'{method} {path}: HTTP {error.code} {detail}') from None
            except urllib.error.URLError as error:
                if attempt < self.retries:
                    time.sleep(min(60, 2 ** attempt * 2))
                    continue
                raise PublishError(f'{method} {path}: {error.reason}') from None

    def get(self, key):
        return self.call('GET', '/values/' + urllib.parse.quote(key, safe=''), raw=True)

    def put_many(self, pairs):
        """pairs: [(key, gzip bytes, metadata)], sent in batches within the bulk API's limits."""
        sent, batch, size = 0, [], 0
        def flush():
            nonlocal sent, batch, size
            if not batch:
                return
            result = self.call('PUT', '/bulk', batch)
            failed = (result.get('result') or {}).get('unsuccessful_keys') or []
            if failed:
                raise PublishError(f'{len(failed)} keys were not written, e.g. {failed[:3]}')
            sent += len(batch)
            batch, size = [], 0
        for key, value, metadata in pairs:
            item = {'key': key, 'value': base64.b64encode(value).decode('ascii'), 'base64': True, 'metadata': metadata}
            item_size = len(item['value']) + len(key) + 200
            if batch and (len(batch) >= BATCH_PAIRS or size + item_size > BATCH_BYTES):
                flush()
            batch.append(item)
            size += item_size
        flush()
        return sent

    def delete_many(self, keys):
        for n in range(0, len(keys), 10000):
            result = self.call('POST', '/bulk/delete', keys[n:n + 10000])
            failed = (result.get('result') or {}).get('unsuccessful_keys') or []
            if failed:
                raise PublishError(f'{len(failed)} keys were not deleted, e.g. {failed[:3]}')
        return len(keys)


def read_manifest(kv: KV):
    raw = kv.get(MANIFEST_KEY)
    if not raw:
        return {}
    try:
        return json.loads(gzip.decompress(raw)).get('keys') or {}
    except (OSError, ValueError) as error:
        print(f'warning: the published manifest could not be read ({error}); sending every key', file=sys.stderr)
        return {}


def publish(entries, kv: KV, *, dry_run=False, log=print):
    previous = read_manifest(kv)
    write, delete = plan(entries, previous)
    log(f'{len(entries):,} keys in the build; {len(previous):,} in the last publish; {len(write):,} to write, {len(delete):,} to delete')
    if dry_run:
        return {'keys': len(entries), 'write': len(write), 'delete': len(delete), 'dryRun': True}
    # pages first, the manifest last: a run that stops part-way leaves the old manifest, so the next run resends
    sent = kv.put_many((k, packed(entries[k]['path']), {'t': entries[k]['t'], 'h': entries[k]['h']}) for k in write)
    removed = kv.delete_many(delete) if delete else 0
    manifest = gzip.compress(json.dumps({'version': 1, 'published': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                         'keys': manifest_of(entries)}, separators=(',', ':')).encode('utf-8'), mtime=0)
    kv.put_many([(MANIFEST_KEY, manifest, {'t': 'application/json', 'h': hashlib.sha256(manifest).hexdigest()[:16]})])
    log(f'wrote {sent:,} keys, deleted {removed:,}, manifest updated')
    return {'keys': len(entries), 'write': sent, 'delete': removed, 'dryRun': False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--site', type=Path, required=True, help='a build from build_well_pages.py --layout site')
    parser.add_argument('--mount', help='URL path the build is served under (default: build.json basePath)')
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--to-folder', type=Path, help='write the local KV stand-in (keys.json + values/) here')
    target.add_argument('--kv', action='store_true', help='upload to Workers KV (see the environment variables above)')
    parser.add_argument('--dry-run', action='store_true', help='with --kv: compare with the last publish and stop')
    parser.add_argument('--summary', type=Path, help='append a Markdown line to this file (GitHub step summary)')
    args = parser.parse_args(argv)
    try:
        entries, mount = collect(args.site, args.mount)
        if args.to_folder:
            count = to_folder(entries, args.to_folder)
            print(f'{count:,} keys under {mount}/ written to {args.to_folder}')
            return 0
        env = {name: os.environ.get(name, '').strip() for name in ('CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_API_TOKEN', 'WELLS_KV_NAMESPACE_ID')}
        missing = [name for name, value in env.items() if not value]
        if missing:
            raise PublishError('missing environment: ' + ', '.join(missing))
        kv = KV(env['CLOUDFLARE_ACCOUNT_ID'], env['WELLS_KV_NAMESPACE_ID'], env['CLOUDFLARE_API_TOKEN'])
        result = publish(entries, kv, dry_run=args.dry_run)
        if args.summary:
            with args.summary.open('a', encoding='utf-8') as stream:
                stream.write(f'\n- **Workers KV**: {result["keys"]:,} keys under `{mount}/`; {result["write"]:,} written, {result["delete"]:,} deleted'
                             f'{" (dry run)" if result["dryRun"] else ""}\n')
    except (PublishError, OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
