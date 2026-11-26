"""Montana wells beside North Dakota's on bakkensubsurface.com/wells/ (build_well_pages.py --layout site --montana ...).

The input is `montana-well-index.json.gz`, which the project's pipeline builds from the Montana Board of Oil and Gas
Conservation's free downloads (`explorer/prepare_montana_well_index.py` there) and publishes as an asset of this
repository's `well-index-data` release. One entry per well in the Board's well list, every county:

- the Board's well facts (operator, status, type, slant, county, township and range with their directions, section and
  quarter call, field, permit, spud and completion dates, total depth, latitude and longitude, lease or unit);
- monthly oil, gas, water and producing days for the whole well, as the Board's production file holds them (amended
  figures included; gas sold and flared gas are not in the file and read as unknown);
- where the well's scanned file was read by machine, the completion report block the 3D viewer's well card shows
  (casing, intervals, stages, sand and water, the test), and the frac water its operator disclosed to FracFocus.

Montana has no file number: the API number is the key, and the page address is the first ten digits, as for North
Dakota (`/wells/25-083-21866/`). Nothing here makes an engineering call, a forecast or a ranking.
"""
from __future__ import annotations

import gzip
import html
import json
from pathlib import Path

esc = lambda value: html.escape('' if value is None else str(value), quote=True)

STATE = 'MT'
BOARD = 'Montana Board of Oil and Gas Conservation'
BOARD_URL = 'https://bogwebfiles.dnrc.mt.gov/'
PRODUCTION_URL = 'https://bogfiles.dnrc.mt.gov/Reporting/Production/Historical/'
# Montana's part of the Williston Basin, as the 3D viewer's Montana layer defines it (prepare_montana_viewer.py)
BASIN_COUNTIES = {'Daniels', 'Dawson', 'Fallon', 'Garfield', 'McCone', 'Prairie', 'Richland', 'Roosevelt', 'Sheridan', 'Valley', 'Wibaux'}
ACTIVE = {'Producing', 'Active Injection'}
PERMIT_ONLY = {'Permit to Drill', 'Permitted Injection Well'}
PLUGGED = {'P&A - Approved', 'Abandoned', 'Abandoned - Unapproved'}
KIND_LABEL = {'conductor': 'Conductor', 'surface': 'Surface', 'dakota': 'Dakota', 'intermediate': 'Intermediate', 'production': 'Production'}


def load(path) -> dict:
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as handle:
        data = json.load(handle)
    if data.get('version') != 1 or data.get('kind') != 'montana-well-index' or not isinstance(data.get('wells'), list):
        raise ValueError(f'{path} is not a version 1 montana-well-index file')
    return data


def format_api(api10: str, api14: str | None = None) -> str:
    digits = (api14 or api10 or '')
    text = f'{digits[:2]}-{digits[2:5]}-{digits[5:10]}'
    return text + (f'-{digits[10:12]}-{digits[12:14]}' if len(digits) >= 14 else '')


def legal_text(record) -> str:
    """'SE NW NW Sec 2 T7S R10W', the form townships.parse_legal reads; '' when the Board lists no township."""
    if not (record.get('twp') and record.get('twpDir') and record.get('rng') and record.get('rngDir')):
        return ''
    parts = [record.get('qtr') or '', f'Sec {record["sec"]}' if record.get('sec') else '', f'T{record["twp"]}{record["twpDir"]}', f'R{record["rng"]}{record["rngDir"]}']
    return ' '.join(p for p in parts if p)


def to_wells(data) -> list[dict]:
    """The Montana entries as well records shaped like the North Dakota ones (build_well_pages.normalize_record)."""
    wells = []
    for record in data['wells']:
        api10 = record['api']
        extras = {}
        for key, field in (('field', 'field'), ('td', 'td'), ('spud', 'spudded'), ('completion', 'completed'), ('permit_date', 'permitted')):
            if record.get(field) not in (None, ''):
                extras[key] = record[field]
        status = record.get('status') or ''
        well = {
            'file': api10, 'api': api10, 'api_full': record.get('api14') or api10, 'api_display': format_api(api10, record.get('api14')),
            'name': record.get('name') or '', 'operator': record.get('operator') or '', 'status': status, 'type': record.get('type') or '',
            'county': record.get('county') or '', 'legal': legal_text(record), 'footages': '', 'lat': record.get('lat'), 'lon': record.get('lon'),
            'confidential': False, 'extras': extras, 'attributes': {}, 'source': 'montana', 'state': STATE,
            'active': status in ACTIVE, 'permit_only': status in PERMIT_ONLY, 'plugged': status in PLUGGED,
            'mt': record,
        }
        production = record.get('production')
        if production:
            n = len(production['v']['oil'])
            production = {**production, 'v': {**production['v'], 'gasSold': [None] * n, 'flared': [None] * n}}
            well['production'] = production
        wells.append(well)
    return wells


def production_context(data) -> dict:
    """What production.well_production_panel needs to say where a Montana well's months come from."""
    p = data['production']
    retrieved = (data.get('source') or {}).get('retrieved')
    return {'firstMonth': p['firstMonth'], 'lastMonth': p['lastMonth'], 'generated': data.get('generated'), 'state': STATE,
            'sourceHtml': f'<a href="{PRODUCTION_URL}" rel="noopener">{BOARD} production file</a>',
            'retrieved': retrieved}


def tone(well) -> str:
    if well['active']:
        return 'ok'
    if well['plugged'] or well['type'] == 'Dry Hole':
        return 'off'
    return 'neutral'


def number(value, digits=0) -> str:
    if value is None:
        return '—'
    return f'{value:,.{digits}f}'


def pipe(size) -> str:
    """5.5 -> 5½; 9.625 -> 9⅝; others as printed."""
    fractions = {0.5: '½', 0.625: '⅝', 0.375: '⅜', 0.875: '⅞', 0.75: '¾', 0.25: '¼', 0.125: '⅛'}
    whole, part = int(size), round(size - int(size), 3)
    if part == 0:
        return f'{whole}'
    return f'{whole}{fractions[part]}' if part in fractions else f'{size:g}'


def completion_panel(well, notes, format_date=lambda iso: iso) -> str:
    """The completion report block, as the viewer's card shows it, from the Board's scanned file read by machine."""
    record = well['mt']
    report, fracfocus = record.get('completion'), record.get('fracfocus')
    if not report and not fracfocus:
        return ''
    file_url = record.get('wellFile')
    rows = []
    if report:
        page = report.get('page')
        where = (f'Page {page} of the <a href="{esc(file_url)}" rel="noopener">Board\'s scanned well file</a>' if page and file_url
                 else f'The <a href="{esc(file_url)}" rel="noopener">Board\'s scanned well file</a>')
        more = f' The file holds {report["reports"]} completion reports; the first is shown.' if (report.get('reports') or 1) > 1 else ''
        rows.append(f'<p class="sub">{where}, read by machine.{more}</p>')
        facts = []
        if report.get('completed'):
            facts.append(('Completed (as printed)', f'<time datetime="{esc(report["completed"])}">{esc(format_date(report["completed"]))}</time>'))
        if report.get('stages'):
            facts.append(('Frac stages', number(report['stages'])))
        if report.get('sand'):
            facts.append(('Sand', f'{number(report["sand"])} lb'))
        if report.get('water'):
            facts.append(('Water', f'{number(report["water"])} bbl'))
        test = report.get('test')
        if test:
            bits = [f'{number(test.get(k))} {unit}' for k, unit in (('oil', 'bbl oil'), ('gas', 'Mcf gas'), ('water', 'bbl water')) if test.get(k) is not None]
            facts.append(('Test on completion', ', '.join(bits) + (f' in {number(test["hours"])} hours' if test.get('hours') else '')))
        if facts:
            rows.append('<dl class="facts">' + ''.join(f'<dt>{label}</dt><dd>{value}</dd>' for label, value in facts) + '</dl>')
        casing = report.get('casing') or []
        if casing:
            # three columns, so the table reads on a phone: the pipe's size, weight and grade go together
            body = ''.join(
                f'<tr><td>{esc(KIND_LABEL.get(c.get("kind"), c.get("kind") or "—"))}</td>'
                f'<td>{esc(", ".join(x for x in (pipe(c["size"]) + " in", (number(c["weight"], 1) + " lb/ft") if c.get("weight") else "", c.get("grade") or "") if x))}</td>'
                f'<td>{number(c.get("top")) + " – " if c.get("top") else ""}{number(c.get("bottom"))} ft</td></tr>' for c in casing)
            rows.append('<h3>Casing</h3><table class="attrs casing"><thead><tr><th>String</th><th>Pipe</th><th>Set at</th></tr></thead>'
                        f'<tbody>{body}</tbody></table>')
        intervals = report.get('intervals') or []
        if intervals:
            items = ''.join(f'<li>{number(i["top"])} – {number(i["bottom"])} ft{" · open hole" if i.get("openHole") else ""}</li>' for i in intervals)
            rows.append(f'<h3>Perforated or open-hole intervals</h3><ul class="plain">{items}</ul>')
    if fracfocus and fracfocus.get('waterGallons'):
        gallons = fracfocus['waterGallons']
        rows.append('<h3>FracFocus</h3><dl class="facts">'
                    f'<dt>Frac water disclosed</dt><dd>{number(gallons)} gal ({number(gallons / 42)} bbl)</dd>'
                    + (f'<dt>Job start</dt><dd><time datetime="{esc(fracfocus["jobStart"])}">{esc(format_date(fracfocus["jobStart"]))}</time></dd>' if fracfocus.get('jobStart') else '')
                    + '</dl>' + (f'<p class="note">{esc(notes.get("fracfocusNote"))}</p>' if notes.get('fracfocusNote') else ''))
    note = ' '.join(esc(n) for n in (notes.get('note'), notes.get('kindNote') if report and report.get('casing') else None) if n)
    return (f'<section class="panel completion"><h2>Completion report</h2>{"".join(rows)}'
            + (f'<p class="note">{note}</p>' if note else '') + '</section>')


def well_page(ctx, w, *, shell, location_html, nearby_html, production_html, units_html=''):
    base = ctx['base_path']
    record = w['mt']
    path = f'{ctx["well_dir"]}/{w["slug"]}/'
    api_text = w['api_display']
    county_label = f'{w["county"]} County' if w['county'] else 'Montana'
    facts = [('Operator', f'<a href="{base}/operators/{w["operator_slug"]}/">{esc(w["operator"] or "Unknown operator")}</a>'),
             ('Status', esc(w['status'] or 'Status not published'))]
    if w['type']:
        facts.append(('Well type', esc(w['type'])))
    if record.get('slant'):
        facts.append(('Direction', esc(record['slant'])))
    if record.get('field'):
        facts.append(('Field', esc(record['field'])))
    for key, label in (('permitted', 'Permit date'), ('spudded', 'Spud date'), ('completed', 'Completion date')):
        if record.get(key):
            facts.append((label, f'<time datetime="{esc(record[key])}">{esc(ctx["format_date"](record[key]))}</time>'))
    if record.get('td'):
        facts.append(('Total depth', f'{record["td"]:,} ft'))
    if record.get('leaseUnit'):
        facts.append(('Lease or unit', esc(record['leaseUnit'])))
    if w['legal']:
        facts.append(('Legal location', ctx['legal_link'](ctx, w)))
    facts.append(('County', f'<a href="{base}/counties/{w["county_slug"]}/">{esc(county_label)}</a>'))
    facts.append(('API no.', f'<code>{esc(api_text)}</code>'))
    if record.get('wellFile'):
        facts.append(('Well file', f'<a href="{esc(record["wellFile"])}" rel="noopener">Board\'s scanned well file (PDF)</a>'))
    facts_html = ''.join(f'<dt>{label}</dt><dd>{value}</dd>' for label, value in facts)

    badges = [f'<span class="badge {tone(w)}">{esc(w["status"] or "Status not published")}</span>']
    if w['type']:
        badges.append(f'<span class="badge neutral">{esc(w["type"])}</span>')
    if (record.get('slant') or '').startswith('Horizontal'):
        badges.append('<span class="badge neutral">Horizontal</span>')
    if record.get('spudded'):
        badges.append(f'<span class="badge neutral">Spud {esc(record["spudded"][:4])}</span>')

    in_basin = w['county'] in BASIN_COUNTIES
    viewer_html = ('<h2>See it in 3D</h2><div class="actions">'
                   + (f'<a class="btn" href="/viewer/?dataset=full&amp;well={esc(w["api"])}">Open in the regional 3D viewer<small>Montana\'s Williston Basin wells, '
                      'drawn on the state\'s simplified lines beside North Dakota\'s surveyed wells</small></a>' if in_basin else '')
                   + '<a class="btn" href="/montana/">Montana on Bakken Subsurface<small>The state\'s wells, production and what the site shows</small></a></div>')
    notes = ctx.get('montana_notes') or {}
    body = (
        f'<p class="eyebrow">Montana · API {esc(api_text)}</p><h1>{esc(w["display_name"])}</h1>'
        f'<p class="lede">Operated by <b>{esc(w["operator"] or "an unlisted operator")}</b> · {esc(county_label)}, Montana</p>'
        f'<div class="badges">{"".join(badges)}</div>'
        f'<div class="grid"><section class="panel"><h2>Well facts</h2><dl class="facts">{facts_html}</dl></section>'
        f'<section class="panel"><h2>Location &amp; navigation</h2>{location_html}{viewer_html}</section></div>'
        f'{production_html}{completion_panel(w, notes, ctx["format_date"])}{units_html}{nearby_html}'
        f'<script src="{base}/assets/page.js" defer></script>'
        + (f'<script type="module" src="{base}/assets/production.js"></script>' if w.get('production') else '')
    )
    description = (f'{w["display_name"]}: {(w["type"] or "well").lower()} operated by {w["operator"] or "an unlisted operator"} in {county_label}, '
                   f'Montana. API {api_text}. Status: {w["status"] or "not published"}. Location, map links, production and nearby wells.')
    title = f'{w["display_name"]} · API {api_text} · {county_label}, MT'
    jsonld = [{
        '@context': 'https://schema.org', '@type': 'Place', 'name': w['display_name'], 'url': ctx['base_url'] + path,
        'identifier': [{'@type': 'PropertyValue', 'propertyID': 'API', 'value': api_text}],
        'address': {'@type': 'PostalAddress', 'addressLocality': county_label, 'addressRegion': 'MT', 'addressCountry': 'US'},
        'additionalProperty': [{'@type': 'PropertyValue', 'name': 'Operator', 'value': w['operator']}],
    }, {
        '@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
            {'@type': 'ListItem', 'position': 1, 'name': 'Wells', 'item': ctx['base_url'] + '/'},
            {'@type': 'ListItem', 'position': 2, 'name': f'{county_label}, MT', 'item': f'{ctx["base_url"]}/counties/{w["county_slug"]}/'},
            {'@type': 'ListItem', 'position': 3, 'name': w['display_name'], 'item': ctx['base_url'] + path}]}]
    if w['lat'] is not None:
        jsonld[0]['geo'] = {'@type': 'GeoCoordinates', 'latitude': round(w['lat'], 5), 'longitude': round(w['lon'], 5)}
    crumbs = [('Wells', f'{base}/'), (f'{county_label}, MT', f'{base}/counties/{w["county_slug"]}/'), (w['display_name'], None)]
    return shell(ctx, title=title, description=description, path=path, body=body, crumbs=crumbs, jsonld=jsonld,
                 sources=montana_sources(ctx))


def montana_sources(ctx) -> str:
    info = ctx.get('montana_source') or {}
    return (f'<p>Well records and production: {BOARD}, free public files retrieved {esc(info.get("retrieved") or "")}. '
            'Status and well type are the Board\'s own words. Locations are published well heads; laterals can run miles from them. '
            'Independent project, not affiliated with the Board.</p>'
            f'<p><a href="{BOARD_URL}" rel="noopener">Source data</a> · <a href="/methodology/">Methodology</a> · '
            f'<a href="https://github.com/calebdanielalbertson-design/bakken-wells" rel="noopener">Code</a> · built <!--wells:built--></p>')
