"""Decide whether a scheduled rebuild would change the published site.

    python scripts/wells/changed.py --live https://.../build.json --provenance work/dmr/download-provenance.json \
        [--data work/data/well-index-data.json.gz] [--generator <commit>] [--github-output "$GITHUB_OUTPUT"]

Compares what this run downloaded with what the live site was built from (its build.json):
the SHA-256 of every DMR archive, the generation time of the spacing-unit data file, and the
generator commit. Prints one line per input and writes `unchanged=true|false` to the GitHub
Actions output file when given. Anything that cannot be compared (no live build.json yet, a
network error, a missing field) counts as changed, so the site is rebuilt rather than left stale.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import urllib.request
from pathlib import Path


def read_json(source: str, timeout: int = 30):
    """A JSON document from a URL or a local path."""
    if source.startswith(('http://', 'https://')):
        request = urllib.request.Request(source, headers={'User-Agent': 'bakken-wells build (changed.py)', 'Cache-Control': 'no-cache'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    return json.loads(Path(source).read_text(encoding='utf-8'))


def data_generated(path: Path | None):
    if not path:
        return None
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as handle:
        return json.load(handle).get('generated')


def compare(live: dict, provenance: dict, generated, generator):
    """Returns (unchanged, lines). Every input that differs or cannot be compared is a change."""
    lines = []
    unchanged = True
    live_sha = {a.get('kind') or a.get('file'): a.get('sha256') for a in live.get('archives', [])}
    new = provenance.get('archives', [])
    if not new:
        return False, ['no archives in the download record']
    for archive in new:
        kind = archive.get('kind') or archive.get('name') or archive.get('file')
        before, after = live_sha.get(kind), archive.get('sha256')
        same = bool(after) and before == after
        unchanged &= same
        lines.append(f'{kind}: {"unchanged" if same else "changed"} ({(after or "?")[:12]} vs live {(before or "none")[:12]})')
    live_generated = (live.get('data') or {}).get('generated')
    same = live_generated == generated
    unchanged &= same
    lines.append(f'unit data: {"unchanged" if same else "changed"} ({generated or "none"} vs live {live_generated or "none"})')
    if generator:
        live_generator = live.get('generator')
        same = live_generator == generator
        unchanged &= same
        lines.append(f'generator: {"unchanged" if same else "changed"} ({generator[:12]} vs live {(live_generator or "none")[:12]})')
    return unchanged, lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--live', required=True, help='URL or path of the live site\'s build.json')
    parser.add_argument('--provenance', required=True, type=Path, help='download-provenance.json from fetch_dmr.py')
    parser.add_argument('--data', type=Path, help='well-index-data.json.gz, if the build will use one')
    parser.add_argument('--generator', help='commit the generator runs from (GITHUB_SHA)')
    parser.add_argument('--github-output', type=Path, help='append unchanged=true|false to this file')
    args = parser.parse_args(argv)
    try:
        live = read_json(args.live)
        provenance = read_json(str(args.provenance))
        generated = data_generated(args.data)
        unchanged, lines = compare(live, provenance, generated, args.generator)
    except Exception as error:  # noqa: BLE001 - any failure means "rebuild", never "skip"
        unchanged, lines = False, [f'could not compare with the live site ({error.__class__.__name__}: {error}); rebuilding']
    for line in lines:
        print(f'- {line}')
    print('nothing changed since the live build; skipping' if unchanged else 'inputs changed; building')
    if args.github_output:
        with args.github_output.open('a', encoding='utf-8') as stream:
            stream.write(f'unchanged={"true" if unchanged else "false"}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
