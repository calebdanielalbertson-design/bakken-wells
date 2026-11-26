"""What changed in the well index, day by day: /wells/changes/ on bakkensubsurface.com (build --layout site).

The build compares today's search index with the one live on the site (fetched before the build) and records each
difference as an event:

    new       a well record that was not there yesterday (in North Dakota mostly new permits)
    status    the state's status changed (permitted to drilling, active to inactive, plugged ...)
    operator  the operator of record changed
    renamed   the well name changed
    removed   a record the state no longer lists

plus a note when production reaches a newer month. Events are added to the feed that is live (also fetched before
the build), newest day first, and the feed keeps KEEP_DAYS days. A rerun on the same day merges into that day.

Written: /wells/changes/index.json (the feed), /wells/changes/feed.xml (Atom, one entry per day) and the page
/wells/changes/. Only these three keys change with the feed, so it adds three KV writes a day.
"""
from __future__ import annotations

import gzip
import html
import json
from datetime import date
from pathlib import Path

VERSION = 1
KEEP_DAYS = 45
DAY_EVENT_LIMIT = 3000      # a day with more (a re-keyed layer, say) keeps its counts and the first events only
PAGE_DAYS = 14
PAGE_ROWS = 150
SITE = 'https://bakkensubsurface.com'
KINDS = [('new', 'New wells and permits'), ('status', 'Status changes'), ('operator', 'Operator changes'),
         ('renamed', 'Renamed'), ('removed', 'No longer listed')]
esc = html.escape


def read_json(path):
    if not path:
        return None
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return None
    raw = path.read_bytes()
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    try:
        return json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None


def format_api(api):
    api = str(api or '')
    return f'{api[:2]}-{api[2:5]}-{api[5:]}' if len(api) == 10 and api.isdigit() else None


def records(index) -> dict:
    """slug -> {name, api, operator, county, state, status, statusText} from a search index (build_well_pages)."""
    if not index or 'wells' not in index:
        return {}
    f = {name: i for i, name in enumerate(index['fields'])}
    ops, counties, states = index.get('operators', []), index.get('counties', []), index.get('countyStates', [])
    statuses, gloss = index.get('statuses', []), index.get('statusGloss', {})
    out = {}
    for row in index['wells']:
        api = row[f['api']]
        slug = row[f['slug']] or format_api(api) or f'file-{row[f["file"]]}'
        county_i = row[f['county']]
        state = (states[county_i] if county_i is not None and county_i < len(states) else None) or ('MT' if str(api or '').startswith('25') else 'ND')
        status = statuses[row[f['status']]] if row[f['status']] is not None else None
        out[slug] = {
            'name': row[f['name']] or None, 'api': format_api(api), 'state': state,
            'operator': ops[row[f['op']]] if row[f['op']] is not None else None,
            'county': counties[county_i] if county_i is not None else None,
            'status': status, 'statusText': (gloss.get(status, status) if state == 'ND' else status),
        }
    return out


def diff(previous_index, index) -> list[dict]:
    before, after = records(previous_index), records(index)
    if not before:
        return []
    events = []

    def event(kind, slug, rec, **extra):
        events.append({'type': kind, 'slug': slug, 'state': rec['state'], 'api': rec['api'], 'name': rec['name'],
                       'operator': rec['operator'], 'county': rec['county'], 'status': rec['statusText'], **extra})

    for slug, rec in after.items():
        old = before.get(slug)
        if old is None:
            event('new', slug, rec)
            continue
        if old['status'] != rec['status']:
            event('status', slug, rec, **{'from': old['statusText'], 'to': rec['statusText']})
        if old['operator'] != rec['operator']:
            event('operator', slug, rec, **{'from': old['operator'], 'to': rec['operator']})
        if old['name'] != rec['name']:
            event('renamed', slug, rec, **{'from': old['name'], 'to': rec['name']})
    for slug, old in before.items():
        if slug not in after:
            event('removed', slug, old)
    order = {k: i for i, (k, _) in enumerate(KINDS)}
    events.sort(key=lambda e: (order[e['type']], e['state'], e['county'] or '', e['name'] or ''))
    return events


def update(previous_feed, events, *, day: str, production: dict, had_previous: bool) -> dict:
    """The feed with today's events merged in (same-day reruns merge), trimmed to KEEP_DAYS days."""
    feed = previous_feed if previous_feed and previous_feed.get('version') == VERSION else {'version': VERSION, 'days': []}
    days = [d for d in feed.get('days', []) if d.get('date') != day]
    today = next((d for d in feed.get('days', []) if d.get('date') == day), None)
    merged = list(today['changes']) if today else []
    seen = {(e['type'], e['slug'], e.get('to')) for e in merged}
    merged += [e for e in events if (e['type'], e['slug'], e.get('to')) not in seen]
    counts = {kind: 0 for kind, _ in KINDS}
    for e in merged:
        counts[e['type']] += 1
    if today:   # counts of events cut by the limit on an earlier run that day stay counted
        for kind in counts:
            counts[kind] = max(counts[kind], (today.get('counts') or {}).get(kind, 0))
    notes = list(today.get('notes', [])) if today else []
    before = feed.get('productionThrough') or {}
    for state, month in sorted((production or {}).items()):
        if month and before.get(state) and month > before[state]:
            note = {'type': 'production', 'state': state, 'from': before[state], 'to': month}
            if note not in notes:
                notes.append(note)
    if not had_previous and not today:
        notes.append({'type': 'start', 'text': 'No earlier index to compare with; changes are recorded from the next build.'})
    entry = {'date': day, 'counts': counts, 'total': sum(counts.values()), 'truncated': len(merged) > DAY_EVENT_LIMIT,
             'changes': merged[:DAY_EVENT_LIMIT], 'notes': notes}
    days = sorted(days + [entry], key=lambda d: d['date'], reverse=True)[:KEEP_DAYS]
    return {'version': VERSION, 'updated': day, 'keepDays': KEEP_DAYS,
            'about': ('Day-by-day differences in the bakkensubsurface.com well index: new well records (in North Dakota mostly new '
                      'permits), status, operator and name changes, and records the state no longer lists, from comparing each '
                      'build with the one before. A day is the build date (UTC), not the date the state made the change.'),
            'productionThrough': {**before, **{k: v for k, v in (production or {}).items() if v}},
            'days': days}


# ---------- the page and the Atom feed ----------
def _label(day: str) -> str:
    d = date.fromisoformat(day)
    return f'{d:%B} {d.day}, {d.year}'


def _summary(entry) -> str:
    parts = [f'{n:,} {label.lower()}' for kind, label in KINDS if (n := entry['counts'].get(kind))]
    for note in entry.get('notes', []):
        if note['type'] == 'production':
            parts.append(f'{note["state"]} production now through {note["to"]}')
    return '; '.join(parts) or 'no changes'


def page_body(feed, *, well_root: str) -> str:
    days = feed['days'][:PAGE_DAYS]
    blocks = []
    for entry in days:
        rows = []
        for kind, label in KINDS:
            items = [e for e in entry['changes'] if e['type'] == kind]
            if not items:
                continue
            shown = items[:PAGE_ROWS]
            body = ''.join(
                f'<tr><td><a href="{well_root}/{esc(e["slug"])}/">{esc(e["name"] or e["slug"])}</a></td>'
                f'<td>{esc(e["operator"] or "")}</td><td>{esc((e["county"] or "") + (", " + e["state"] if e["state"] else ""))}</td>'
                f'<td>{esc(e["from"] + " → " + e["to"]) if e.get("from") is not None and e.get("to") is not None else esc(e["status"] or "")}</td></tr>'
                for e in shown)
            more = entry['counts'][kind] - len(shown)
            rows.append(f'<h3>{esc(label)} <small>{entry["counts"][kind]:,}</small></h3><div class="scroll"><table class="units changes"><thead><tr>'
                        f'<th>Well</th><th>Operator</th><th>County</th><th>{"Change" if kind in ("status", "operator", "renamed") else "Status"}</th></tr></thead>'
                        f'<tbody>{body}</tbody></table></div>'
                        + (f'<p class="note">And {more:,} more in <a href="{well_root}/changes/index.json">the feed</a>.</p>' if more > 0 else ''))
        notes = ''.join(f'<p class="note">{esc(n["state"])} production now runs through {esc(n["to"])} (was {esc(n["from"])}).</p>'
                        for n in entry.get('notes', []) if n['type'] == 'production')
        notes += ''.join(f'<p class="note">{esc(n["text"])}</p>' for n in entry.get('notes', []) if n['type'] == 'start')
        blocks.append(f'<section class="panel" id="day-{entry["date"]}"><h2>{_label(entry["date"])}</h2>'
                      f'<p class="sub">{esc(_summary(entry))}.</p>{notes}{"".join(rows)}</section>')
    intro = ('<p class="eyebrow">Find a well · What changed</p><h1>What changed in the well records</h1>'
             '<p class="lede">New wells and permits, status and operator changes, renamed wells and records the states no longer list, '
             'found by comparing each daily build of the index with the one before.</p>'
             f'<p class="note">A day is the date of the build, not the date the state recorded the change. The last {KEEP_DAYS} days are in '
             f'<a href="{well_root}/changes/index.json">the feed (JSON)</a> and <a href="{well_root}/changes/feed.xml">the Atom feed</a> for a reader, '
             'and at <code>/api/v1/changes</code>.</p>')
    return intro + (''.join(blocks) or '<section class="panel"><p class="note">No changes recorded yet.</p></section>')


def atom(feed, *, well_root_url: str) -> str:
    updated = feed.get('updated') or date.today().isoformat()
    entries = []
    for entry in feed['days']:
        lines = [f'{e["name"] or e["slug"]} ({e["county"] or "?"}, {e["state"]}): '
                 + (f'{e["from"]} → {e["to"]}' if e.get('from') is not None and e.get('to') is not None else (e['status'] or e['type']))
                 for e in entry['changes'][:25]]
        content = f'<p>{esc(_summary(entry))}.</p>' + ('<ul>' + ''.join(f'<li>{esc(x)}</li>' for x in lines) + '</ul>' if lines else '')
        entries.append(
            f'<entry><id>{well_root_url}/changes/#day-{entry["date"]}</id><title>{esc(_label(entry["date"]))}: {esc(_summary(entry))}</title>'
            f'<updated>{entry["date"]}T12:00:00Z</updated><link href="{well_root_url}/changes/#day-{entry["date"]}"/>'
            f'<content type="html">{esc(content)}</content></entry>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">'
            f'<id>{well_root_url}/changes/</id><title>Bakken Subsurface: what changed in the well records</title>'
            f'<link href="{well_root_url}/changes/"/><link rel="self" href="{well_root_url}/changes/feed.xml"/>'
            f'<updated>{updated}T12:00:00Z</updated><author><name>Bakken Subsurface Analytics</name></author>'
            + ''.join(entries) + '</feed>\n')
