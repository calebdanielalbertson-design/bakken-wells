"""Build one static page per North Dakota well from the public NDIC/DMR wells layer.

    python scripts/wells/build_well_pages.py --source work/dmr --out work/wells-site --base-url https://wells.example.com
    python scripts/wells/build_well_pages.py --inspect work/dmr/OGD_Wells.zip        # print the layer's fields

Input is what fetch_dmr.py downloads: a folder holding OGD_Wells.zip (plus the optional permits
archive and download-provenance.json), or a single .zip. Output is a complete static site:

    /                      search page (fuzzy, client side, over /wells/index.json)
    /wells/<api>/          one page per well, e.g. /wells/33-053-04567/
    /operators/<slug>/     every operator, wells grouped by county
    /counties/<slug>/      every county: operators, recent spuds, permitted locations
    /units/<id>/           one page per spacing unit (with --data; see wells/units.py)
    /orders/<no>/          one page per NDIC order matched to a unit (with --data)
    sitemap.xml            sitemap index -> sitemap-pages.xml + sitemap-wells-N.xml (+ units, orders)
    build.json             counts, field mapping, warnings (also appended to --summary as Markdown)

--data takes the pipeline's well-index-data.json.gz (spacing-unit footprints, membership and
order history); without it the well pages simply have no "Spacing units" panel.

No third-party packages. Every value from the DBF is treated as data and HTML-escaped.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells import production as prod  # noqa: E402
from wells import townships as tws  # noqa: E402
from wells import units as dsu  # noqa: E402
from wells.shapefile import load_layer  # noqa: E402

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / 'templates'
CODES = json.loads((HERE / 'codes.json').read_text(encoding='utf-8'))
MAIN_SITE = 'https://bakkensubsurface.com'
VIEWER_URL = MAIN_SITE + '/viewer/?dataset=full&well={file}'
VIEWER_3D_URL = MAIN_SITE + '/3d/'
DMR_PAGE = 'https://gis.dmr.nd.gov/gisdownload.asp'
WELLS_PER_SITEMAP = 10000
NEARBY_MILES = 3.0
NEARBY_LIMIT = 8

# Canonical field -> candidate DBF names (lower case), first present wins. Confirmed in the
# October 2026 OGD_Wells layer: fileno, api, well_name, operator, status. The rest are best
# guesses; --inspect and build.json report what was and was not mapped so the list can grow.
ALIASES = {
    'file': ('fileno', 'file_no', 'file_number', 'ndic_file', 'permit', 'wl_permit'),
    'api': ('api', 'api_wellno', 'apino', 'api_number', 'api_no'),
    'api_display': ('api_no',),
    'name': ('well_name', 'wellname', 'well_nm', 'name'),
    'operator': ('operator', 'current_op', 'curr_oper', 'oper_name', 'operator_name', 'company'),
    'status': ('status', 'well_stat', 'wellstatus', 'well_status'),
    'type': ('well_type', 'welltype', 'wl_type', 'welltyp', 'type'),
    'county': ('county', 'county_name', 'countyname', 'cnty', 'co_name'),
    'field': ('field', 'fieldname', 'field_name', 'fld_name'),
    'pool': ('pool', 'poolname', 'pool_name'),
    'spud': ('spud_date', 'spud', 'spuddate', 'spud_dt', 'date_spud'),
    'completion': ('comp_date', 'completion', 'completion_date', 'completed', 'date_comp'),
    'permit_date': ('permit_date', 'permitdate', 'date_permit', 'issued', 'issue_date', 'approved'),
    'td': ('td', 'total_depth', 'totaldepth', 'tot_depth'),
    'tvd': ('tvd', 'true_vert', 'tvd_ft'),
    'lat': ('lat', 'latitude', 'lat_dd', 'lat_83', 'y'),
    'lon': ('long', 'lon', 'longitude', 'long_dd', 'lon_dd', 'long_83', 'x'),
    'location': ('location', 'legal', 'legal_desc', 'footages', 'qq_sec_twp', 'loc'),
    'qq': ('qq', 'qtr_qtr', 'quarter'),
    'section': ('section', 'sec', 'sect'),
    'township': ('township', 'twp', 'twn'),
    'range': ('range', 'rng', 'rge'),
    'feet_ns': ('feet_ns', 'footage_ns', 'ns_feet'),
    'fnsl': ('fnsl', 'ns_ref', 'ns_line'),
    'feet_ew': ('feet_ew', 'footage_ew', 'ew_feet'),
    'fewl': ('fewl', 'ew_ref', 'ew_line'),
    'elevation': ('elevation', 'elev', 'gr_elev', 'kb_elev', 'ground_elev'),
    'confidential': ('confidential', 'tight', 'cnf', 'tight_hole'),
    'cum_oil': ('cum_oil', 'cumoil', 'cum_oil_bbl', 'cumulative_oil'),
    'cum_gas': ('cum_gas', 'cumgas', 'cum_gas_mcf'),
    'cum_water': ('cum_water', 'cumwater', 'cum_wtr'),
    'last_prod': ('last_prod', 'lastprod', 'last_prod_date', 'last_production'),
}
LABELS = {
    'field': 'Field', 'pool': 'Pool', 'spud': 'Spud date', 'completion': 'Completion date',
    'permit_date': 'Permit date', 'td': 'Total depth', 'tvd': 'True vertical depth',
    'elevation': 'Elevation', 'cum_oil': 'Cumulative oil', 'cum_gas': 'Cumulative gas',
    'cum_water': 'Cumulative water', 'last_prod': 'Last production',
}
UNITS = {'td': ' ft', 'tvd': ' ft', 'elevation': ' ft', 'cum_oil': ' bbl', 'cum_gas': ' mcf', 'cum_water': ' bbl'}
COUNTY_FIXES = {'Mckenzie': 'McKenzie', 'Mchenry': 'McHenry', 'Mcintosh': 'McIntosh', 'Mclean': 'McLean', 'Lamoure': 'LaMoure'}
ND_BOUNDS = (45.5, 49.2, -104.3, -96.3)  # lat min, lat max, lon min, lon max (generous)
STATUS_TONE = {'A': 'ok', 'DRL': 'ok', 'PA': 'off', 'AB': 'off', 'DRY': 'off', 'PNC': 'off'}

esc = lambda value: html.escape('' if value is None else str(value), quote=True)
slugify = lambda value: re.sub(r'[^a-z0-9]+', '-', str(value or '').lower()).strip('-')[:80] or 'unknown'


class BuildError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- reading


def resolve_fields(field_names):
    """Map canonical keys to the DBF field actually present, if any."""
    present = set(field_names)
    mapping = {}
    for key, candidates in ALIASES.items():
        for candidate in candidates:
            if candidate in present:
                mapping[key] = candidate
                break
    return mapping


def parse_date(value):
    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        return text
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
    if m:
        return f'{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}'
    m = re.fullmatch(r'(\d{4})(\d{2})(\d{2})', text)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    return None


def format_date(iso):
    try:
        if not iso:
            return None
        parsed = datetime.strptime(iso, '%Y-%m-%d')
        return f'{parsed:%b} {parsed.day}, {parsed.year}'
    except ValueError:
        return iso


def http_date(value):
    """'Fri, 06 Nov 2026 09:17:41 GMT' -> '2026-11-06'; None when absent or unparseable."""
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(str(value)).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return None


def format_api(digits):
    if not digits:
        return ''
    if len(digits) < 10:
        return digits
    tail = f'-{digits[10:]}' if len(digits) > 10 else ''
    return f'{digits[:2]}-{digits[2:5]}-{digits[5:10]}{tail}'


def county_name(raw):
    if not raw:
        return ''
    name = ' '.join(part.capitalize() for part in str(raw).strip().split())
    return COUNTY_FIXES.get(name, name)


def plausible(lat, lon):
    return lat is not None and lon is not None and ND_BOUNDS[0] <= lat <= ND_BOUNDS[1] and ND_BOUNDS[2] <= lon <= ND_BOUNDS[3]


def to_float(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def normalize_record(row, mapping, layer_geographic, source_kind):
    get = lambda key: row.get(mapping[key]) if key in mapping else None
    file_raw = get('file')
    file_no = re.sub(r'\.0+$', '', str(file_raw).strip()) if file_raw is not None else ''
    if not re.fullmatch(r'\d+', file_no):
        return None
    api_digits = re.sub(r'\D', '', str(get('api') or ''))
    api10 = api_digits[:10] if len(api_digits) >= 10 else ''
    name = ' '.join(str(get('name') or '').split())
    lat, lon = to_float(get('lat')), to_float(get('lon'))
    if not plausible(lat, lon):
        lat, lon = (None, None)
        if layer_geographic and plausible(row.get('_y'), row.get('_x')):
            lat, lon = row['_y'], row['_x']
    status = str(get('status') or '').strip().upper()
    if source_kind == 'permits' and not status:
        status = 'LOC'
    legal = get('location')
    if not legal:
        parts = [get('qq'), get('section'), get('township'), get('range')]
        if any(p not in (None, '') for p in parts):
            qq, sec, twp, rng = [str(p).strip() if p not in (None, '') else '' for p in parts]
            # Every North Dakota township is north of the baseline and every range west of the 5th meridian.
            legal = ' '.join(x for x in (qq, f'Sec {sec}' if sec else '', f'T{twp}N' if twp else '', f'R{rng}W' if rng else '') if x)
    footages = []
    for feet_key, ref_key in (('feet_ns', 'fnsl'), ('feet_ew', 'fewl')):
        number, ref = to_float(get(feet_key)), str(get(ref_key) or '').strip().upper()
        if number and ref in ('N', 'S', 'E', 'W'):
            footages.append(f'{number:,.0f} ft F{ref}L')
    extras = {}
    for key in ('field', 'pool', 'td', 'tvd', 'elevation', 'cum_oil', 'cum_gas', 'cum_water'):
        value = get(key)
        if value not in (None, ''):
            extras[key] = value
    for key in ('spud', 'completion', 'permit_date', 'last_prod'):
        value = parse_date(get(key))
        if value:
            extras[key] = value
    confidential = get('confidential')
    shown = set(mapping.values())
    attributes = {k: v for k, v in row.items() if not k.startswith('_') and v not in (None, '') and k not in shown}
    return {
        'file': file_no, 'api': api10, 'api_full': api_digits if len(api_digits) > 10 else api10,
        'api_display': ' '.join(str(get('api_display') or '').split()),
        'name': name, 'operator': ' '.join(str(get('operator') or '').split()),
        'status': status, 'type': str(get('type') or '').strip().upper(),
        'county': county_name(get('county')), 'legal': legal, 'footages': ' · '.join(footages), 'lat': lat, 'lon': lon,
        'confidential': status == 'CONFIDENTIAL' or (bool(confidential) if isinstance(confidential, bool) else str(confidential or '').strip().upper() in ('Y', 'YES', 'T', 'TRUE', '1')),
        'extras': extras, 'attributes': attributes, 'source': source_kind,
    }


def load_sources(source: Path, limit=None):
    """Return (wells, report) from a folder with download-provenance.json or a single zip."""
    source = Path(source)
    archives = []
    provenance = None
    if source.is_dir():
        record = source / 'download-provenance.json'
        if record.exists():
            provenance = json.loads(record.read_text(encoding='utf-8'))
            for entry in provenance.get('archives', []):
                path = source / entry['file']
                if path.exists():
                    archives.append((entry.get('kind') or entry.get('name') or 'wells', path, entry))
        if not archives:
            for path in sorted(source.glob('*.zip')):
                archives.append(('permits' if 'permit' in path.name.lower() else 'wells', path, {'file': path.name}))
    else:
        archives.append(('wells', source, {'file': source.name}))
    if not any(kind == 'wells' for kind, _, _ in archives):
        raise BuildError(f'No wells archive found under {source}')

    wells, by_file, report = [], {}, {'archives': [], 'provenance': provenance, 'warnings': []}
    for kind, path, entry in sorted(archives, key=lambda a: a[0] != 'wells'):
        try:
            layer = load_layer(path)
        except (ValueError, OSError, zipfile.BadZipFile) as error:
            if kind == 'wells':
                raise BuildError(f'{path.name}: {error}') from None
            report['warnings'].append(f'{path.name} ({kind}) skipped: {error}')
            continue
        mapping = resolve_fields(layer.field_names)
        if 'file' not in mapping:
            if kind != 'wells':
                report['warnings'].append(f'{path.name} ({kind}) skipped: no file-number field among {layer.field_names}')
                continue
            raise BuildError(f'{path.name}: no file-number field among {layer.field_names}')
        if kind == 'wells':
            for key in ('api', 'name', 'operator', 'status'):
                if key not in mapping:
                    report['warnings'].append(f'{path.name}: no {key} field recognised (fields: {", ".join(layer.field_names)})')
        counts = Counter({key: 0 for key in ('wells', 'with_coordinates', 'without_coordinates', 'without_api', 'without_name',
                                           'invalid_file_number', 'duplicate_file_number', 'enriched')})
        rows = layer.rows[:limit] if limit else layer.rows
        for row in rows:
            well = normalize_record(row, mapping, layer.geographic, kind)
            if well is None:
                counts['invalid_file_number'] += 1
                continue
            if well['file'] in by_file:
                counts['duplicate_file_number'] += 1
                existing = by_file[well['file']]
                if kind == 'wells':
                    # keep the record with the most information
                    if len(well['attributes']) > len(existing['attributes']):
                        existing.update({k: v for k, v in well.items() if k != 'attributes'})
                        existing['attributes'] = well['attributes']
                else:
                    # a supplementary layer (e.g. permit status before spud) adds its own columns to the well
                    existing['attributes'].update({f'{kind}: {k}': v for k, v in well['attributes'].items()})
                    existing.setdefault('layers', []).append(kind)
                    counts['enriched'] += 1
                continue
            by_file[well['file']] = well
            wells.append(well)
            counts['wells'] += 1
            counts['with_coordinates' if well['lat'] is not None else 'without_coordinates'] += 1
            if not well['api']:
                counts['without_api'] += 1
            if not well['name']:
                counts['without_name'] += 1
        report['archives'].append({
            'file': path.name, 'kind': kind, 'layer': layer.name, 'records': len(layer.rows), 'deleted': layer.deleted,
            'shapeType': layer.shape_type, 'geographic': layer.geographic, 'prj': layer.prj[:120],
            'fields': layer.field_names, 'mapped': mapping,
            'unmapped': [f for f in layer.field_names if f not in mapping.values()],
            'counts': dict(counts), 'sha256': entry.get('sha256'), 'retrievedAt': entry.get('retrievedAt'),
            'lastModified': entry.get('lastModified'),
        })
        if not layer.geographic and 'lat' not in mapping:
            report['warnings'].append(f'{path.name}: projected coordinates and no lat/long fields; map links will be missing')
    return wells, report


# ----------------------------------------------------------------------------- derived data


def assign_slugs(wells):
    seen = set()
    for well in wells:
        base = format_api(well['api']) if well['api'] and well['api'].startswith('33') else f'file-{well["file"]}'
        slug = base
        if slug in seen:
            slug = f'{base}-{well["file"]}'
        seen.add(slug)
        well['slug'] = slug
        well['display_name'] = well['name'] or f'Well file {well["file"]}'


def compute_nearby(wells):
    # Cells at least NEARBY_MILES wide in both axes, so checking the 3x3 neighbourhood finds every
    # well head within the radius (cos 47.8 degrees is within 3% across North Dakota).
    cos_lat = math.cos(math.radians(47.8))
    cell_lat = NEARBY_MILES / 69.0
    cell_lon = NEARBY_MILES / (69.0 * cos_lat)
    grid = defaultdict(list)
    located = [w for w in wells if w['lat'] is not None]
    for w in located:
        grid[(int(w['lat'] // cell_lat), int(w['lon'] // cell_lon))].append(w)
    for w in located:
        cx, cy = int(w['lat'] // cell_lat), int(w['lon'] // cell_lon)
        found = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in grid.get((cx + dx, cy + dy), ()):
                    if other is w:
                        continue
                    dlat = (other['lat'] - w['lat']) * 69.0
                    dlon = (other['lon'] - w['lon']) * 69.0 * cos_lat
                    miles = math.hypot(dlat, dlon)
                    if miles <= NEARBY_MILES:
                        found.append((miles, other))
        found.sort(key=lambda item: (item[0], item[1]['file']))
        w['nearby'] = [(round(m, 2), o) for m, o in found[:NEARBY_LIMIT]]
    for w in wells:
        w.setdefault('nearby', [])


def group_operators(wells):
    groups = defaultdict(lambda: {'spellings': Counter(), 'wells': []})
    for w in wells:
        key = slugify(w['operator']) if w['operator'] else 'unknown-operator'
        groups[key]['spellings'][w['operator'] or 'Unknown operator'] += 1
        groups[key]['wells'].append(w)
        w['operator_slug'] = key
    operators = {}
    for key, group in groups.items():
        name = group['spellings'].most_common(1)[0][0]
        operators[key] = {'slug': key, 'name': name, 'wells': group['wells'],
                          'statuses': Counter(w['status'] or '—' for w in group['wells']),
                          'counties': Counter(w['county'] or 'Unknown' for w in group['wells'])}
    return operators


def group_counties(wells):
    counties = {}
    for w in wells:
        name = w['county'] or 'Unknown county'
        key = slugify(name)
        w['county_slug'] = key
        entry = counties.setdefault(key, {'slug': key, 'name': name, 'wells': [], 'operators': Counter(), 'statuses': Counter()})
        entry['wells'].append(w)
        entry['operators'][w['operator_slug']] += 1
        entry['statuses'][w['status'] or '—'] += 1
    return counties


# ----------------------------------------------------------------------------- html


def status_text(code):
    gloss = CODES['status'].get(code)
    return f'{gloss} ({code})' if gloss else (code or 'Status not published')


def type_text(code):
    gloss = CODES['type'].get(code)
    return f'{gloss} ({code})' if gloss else (code or '')


def format_number(value, unit=''):
    number = to_float(value)
    if number is None:
        return esc(value)
    text = f'{number:,.0f}' if abs(number - round(number)) < 1e-9 else f'{number:,.2f}'
    return f'{text}{unit}'


def shell(ctx, *, title, description, path, body, crumbs=None, jsonld=None, robots=None, current=None):
    base = ctx['base_path']
    canonical = ctx['base_url'] + path
    nav = [('/', 'Find a well', 'search'), ('/operators/', 'Operators', 'operators'), ('/counties/', 'Counties', 'counties'),
           ('/townships/', 'Townships', 'townships')]
    if ctx.get('has_data'):
        nav += [('/units/', 'Spacing units', 'units'), ('/orders/', 'Orders', 'orders')]
    nav_html = ''.join(f'<a href="{base}{href}"{' aria-current="page"' if key == current else ''}>{label}</a>' for href, label, key in nav)
    crumb_html = ''
    if crumbs:
        items = [f'<a href="{esc(href)}">{esc(label)}</a>' if href else esc(label) for label, href in crumbs]
        crumb_html = '<nav class="crumbs" aria-label="Breadcrumb">' + items[0] + ''.join(f'<span>{item}</span>' for item in items[1:]) + '</nav>'
    ld = ''
    if jsonld:
        ld = '<script type="application/ld+json">' + json.dumps(jsonld, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/') + '</script>'
    robots_meta = f'<meta name="robots" content="{robots}">' if robots else ''
    # Google Search Console ownership proof for the URL-prefix property (HTML tag method); public by nature
    verification = f'<meta name="google-site-verification" content="{esc(ctx["site_verification"])}">' if ctx.get('site_verification') else ''
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'<title>{esc(title)}</title><meta name="description" content="{esc(description)}">'
        f'<link rel="canonical" href="{esc(canonical)}">{robots_meta}{verification}'
        f'<meta property="og:title" content="{esc(title)}"><meta property="og:url" content="{esc(canonical)}">'
        '<meta name="theme-color" content="#ffffff">'
        f'<link rel="icon" href="{MAIN_SITE}/favicon.svg" type="image/svg+xml">'
        f'<link rel="stylesheet" href="{base}/assets/well.css">{ld}</head><body>'
        '<a class="skip" href="#main">Skip to content</a>'
        f'<header class="top"><div class="wrap"><a class="brand" href="{MAIN_SITE}/"><i>B</i><span>Bakken Subsurface<small>North Dakota well index</small></span></a>'
        f'<nav aria-label="Main navigation">{nav_html}<a href="{MAIN_SITE}/viewer/?dataset=full">3D viewer</a></nav></div></header>'
        f'<main id="main" class="wrap">{crumb_html}{body}</main>'
        f'<footer class="site"><div class="wrap"><p>Well records: {esc(ctx["attribution"])}, public GIS download retrieved {esc(ctx["source_date"])}. '
        'NDIC codes are shown with our plain-language reading. Locations are published well heads; laterals can run miles from them. Independent project, not affiliated with NDIC.</p>'
        f'<p><a href="{DMR_PAGE}" rel="noopener">Source data</a> · <a href="{MAIN_SITE}/methodology/">Methodology</a> · <a href="https://github.com/calebdanielalbertson-design/bakken-wells" rel="noopener">Code</a> · built {esc(ctx["built"])}</p></div></footer>'
        '</body></html>'
    )


def well_page(ctx, w):
    base = ctx['base_path']
    path = f'/wells/{w["slug"]}/'
    api_text = w['api_display'] or (format_api(w['api_full']) if w['api_full'] else '—')
    county_label = f'{w["county"]} County' if w['county'] else 'North Dakota'
    type_gloss = CODES['type'].get(w['type'])
    description = (f'{w["display_name"]}: {(type_gloss.lower() + " well") if type_gloss else "well"} operated by {w["operator"] or "an unlisted operator"} '
                   f'in {county_label}, North Dakota. NDIC file {w["file"]}, API {api_text}. Status: {status_text(w["status"])}. '
                   'Location, map links and nearby wells.')
    title = f'{w["display_name"]} · API {api_text} · {county_label}, ND'

    facts = []
    facts.append(('Operator', f'<a href="{base}/operators/{w["operator_slug"]}/">{esc(w["operator"] or "Unknown operator")}</a>'))
    facts.append(('Status', esc(status_text(w['status']))))
    if w['type']:
        facts.append(('Well type', esc(type_text(w['type']))))
    for key in ('field', 'pool', 'spud', 'completion', 'permit_date', 'td', 'tvd', 'elevation', 'cum_oil', 'cum_gas', 'cum_water', 'last_prod'):
        if key in w['extras']:
            value = w['extras'][key]
            if key in ('spud', 'completion', 'permit_date', 'last_prod'):
                rendered = f'<time datetime="{esc(value)}">{esc(format_date(value))}</time>'
            elif key in UNITS:
                rendered = format_number(value, UNITS[key])
            else:
                rendered = esc(value)
            facts.append((LABELS[key], rendered))
    if w['legal']:
        facts.append(('Legal location', tws.legal_link(ctx, w)))
    if w['footages']:
        facts.append(('Footages', esc(w['footages'])))
    facts.append(('County', f'<a href="{base}/counties/{w["county_slug"]}/">{esc(county_label)}</a>'))
    facts.append(('NDIC file no.', f'<code>{esc(w["file"])}</code>'))
    facts.append(('API no.', f'<code>{esc(api_text)}</code>'))
    facts_html = ''.join(f'<dt>{label}</dt><dd>{value}</dd>' for label, value in facts)

    tone = STATUS_TONE.get(w['status'], 'neutral')
    badges = [f'<span class="badge {tone}">{esc(status_text(w["status"]))}</span>']
    if type_gloss:
        badges.append(f'<span class="badge neutral">{esc(type_gloss)}</span>')
    if 'spud' in w['extras']:
        badges.append(f'<span class="badge neutral">Spud {esc(w["extras"]["spud"][:4])}</span>')
    if w['confidential']:
        badges.append('<span class="badge off">Confidential (tight hole)</span>')
    if w['source'] == 'permits':
        badges.append('<span class="badge neutral">From the permit layer</span>')
    elif 'permits' in w.get('layers', ()):
        badges.append('<span class="badge neutral">In NDIC\'s permit-status layer</span>')

    if w['lat'] is not None:
        lat, lon = f'{w["lat"]:.5f}', f'{w["lon"]:.5f}'
        q = esc(w['display_name'])
        location_html = (
            f'<div class="actions"><a class="btn primary" href="https://www.google.com/maps/dir/?api=1&amp;destination={lat},{lon}" rel="noopener">Navigate with Google Maps<small>Driving directions to the well head</small></a>'
            f'<a class="btn" href="https://www.google.com/maps/search/?api=1&amp;query={lat},{lon}" rel="noopener">Open in Google Maps<small>Satellite view of the pad</small></a>'
            f'<a class="btn" href="https://maps.apple.com/?daddr={lat},{lon}&amp;q={q}" rel="noopener">Open in Apple Maps</a></div>'
            f'<p class="coords"><code>{lat}, {lon}</code><button class="copy" type="button" data-copy="{lat}, {lon}">Copy coordinates</button></p>'
            '<p class="note">Published surface location (NAD83). A horizontal well\'s lateral can run up to three miles from this point.</p>')
    else:
        location_html = '<p class="note">NDIC has not published coordinates for this record, so map links are unavailable.</p>'
    viewer_html = (
        f'<h2>See it in 3D</h2><div class="actions"><a class="btn" href="{VIEWER_URL.format(file=w["file"])}" rel="noopener">Open in the regional 3D viewer<small>Surveyed well path, operator and production; Bakken-system horizontals only</small></a>'
        f'<a class="btn" href="{VIEWER_3D_URL}" rel="noopener">Bakken Subsurface 3D<small>Basin-wide production playback</small></a></div>')

    if w['nearby']:
        items = []
        for miles, other in w['nearby']:
            dist = f'{miles * 5280:,.0f} ft' if miles < 0.25 else f'{miles:.1f} mi'
            items.append(f'<li><a href="{base}/wells/{other["slug"]}/">{esc(other["display_name"])}<small>{esc(other["operator"] or "Unknown operator")} · {esc(status_text(other["status"]))}</small></a><span class="dist">{dist}</span></li>')
        nearby_html = f'<section class="panel"><h2>Nearby wells</h2><ul class="nearby">{"".join(items)}</ul><p class="note">Closest published well heads within {NEARBY_MILES:g} miles; wells on one pad share a location.</p></section>'
    else:
        nearby_html = ''

    attr_rows = ''.join(f'<tr><th>{esc(key)}</th><td>{esc(value)}</td></tr>' for key, value in sorted(w['attributes'].items()))
    attributes_html = (f'<details><summary>Other published attributes ({len(w["attributes"])})</summary>'
                       f'<table class="attrs"><tbody>{attr_rows}</tbody></table><p class="note">DMR shapefile columns not shown above, unchanged.</p></details>') if w['attributes'] else ''

    body = (
        f'<p class="eyebrow">NDIC file {esc(w["file"])} · API {esc(api_text)}</p><h1>{esc(w["display_name"])}</h1>'
        f'<p class="lede">Operated by <b>{esc(w["operator"] or "an unlisted operator")}</b> · {esc(county_label)}, North Dakota</p>'
        f'<div class="badges">{"".join(badges)}</div>'
        f'<div class="grid"><section class="panel"><h2>Well facts</h2><dl class="facts">{facts_html}</dl></section>'
        f'<section class="panel"><h2>Location &amp; navigation</h2>{location_html}{viewer_html}</section></div>'
        f'{prod.well_production_panel(ctx, w)}{dsu.well_units_panel(ctx, w)}{nearby_html}{attributes_html}'
        f'<script src="{base}/assets/page.js" defer></script>'
        + (f'<script type="module" src="{base}/assets/production.js"></script>' if w.get('production') else '')
        + (f'<script type="module" src="{base}/assets/compare.js"></script>' if ctx.get('production') and w.get('units') else '')
    )
    jsonld = [{
        '@context': 'https://schema.org', '@type': 'Place', 'name': w['display_name'], 'url': ctx['base_url'] + path,
        'identifier': [{'@type': 'PropertyValue', 'propertyID': 'API', 'value': api_text},
                       {'@type': 'PropertyValue', 'propertyID': 'NDIC file number', 'value': w['file']}],
        'address': {'@type': 'PostalAddress', 'addressLocality': county_label, 'addressRegion': 'ND', 'addressCountry': 'US'},
        'additionalProperty': [{'@type': 'PropertyValue', 'name': 'Operator', 'value': w['operator']}],
    }, {
        '@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
            {'@type': 'ListItem', 'position': 1, 'name': 'Wells', 'item': ctx['base_url'] + '/'},
            {'@type': 'ListItem', 'position': 2, 'name': county_label, 'item': f'{ctx["base_url"]}/counties/{w["county_slug"]}/'},
            {'@type': 'ListItem', 'position': 3, 'name': w['display_name'], 'item': ctx['base_url'] + path}]}]
    if w['lat'] is not None:
        jsonld[0]['geo'] = {'@type': 'GeoCoordinates', 'latitude': round(w['lat'], 5), 'longitude': round(w['lon'], 5)}
    crumbs = [('Wells', f'{base}/'), (county_label, f'{base}/counties/{w["county_slug"]}/'), (w['display_name'], None)]
    return shell(ctx, title=title, description=description, path=path, body=body, crumbs=crumbs, jsonld=jsonld)


def well_list(ctx, wells, with_operator=False):
    base = ctx['base_path']
    items = []
    for w in sorted(wells, key=lambda x: (x['display_name'], x['file'])):
        detail = f'{w["operator"]} · ' if with_operator and w['operator'] else ''
        items.append(f'<li><a href="{base}/wells/{w["slug"]}/">{esc(w["display_name"])}</a> <small>{esc(detail)}{esc(status_text(w["status"]))}</small></li>')
    return f'<ul class="plain">{"".join(items)}</ul>'


def kpis(pairs):
    return '<div class="kpis">' + ''.join(f'<div class="kpi"><b>{esc(value)}</b><span>{esc(label)}</span></div>' for value, label in pairs) + '</div>'


def operator_page(ctx, op):
    base = ctx['base_path']
    path = f'/operators/{op["slug"]}/'
    wells = op['wells']
    active = sum(1 for w in wells if w['status'] == 'A')
    plugged = sum(1 for w in wells if w['status'] in ('PA', 'AB'))
    permitted = sum(1 for w in wells if w['status'] == 'LOC')
    by_county = defaultdict(list)
    for w in wells:
        by_county[(w['county'] or 'Unknown county', w['county_slug'])].append(w)
    sections = ''.join(
        f'<section class="panel"><h2><a href="{base}/counties/{slug}/">{esc(county)}</a> <small class="mono">{len(group):,}</small></h2>{well_list(ctx, group)}</section>'
        for (county, slug), group in sorted(by_county.items(), key=lambda item: (-len(item[1]), item[0])))
    body = (f'<p class="eyebrow">Operator</p><h1>{esc(op["name"])}</h1>'
            f'<p class="lede">{len(wells):,} well records in the NDIC wells layer, as published under this operator name.</p>'
            + kpis([(f'{len(wells):,}', 'wells'), (f'{active:,}', 'active'), (f'{plugged:,}', 'plugged or abandoned'), (f'{permitted:,}', 'permitted, not drilled'), (f'{len(by_county):,}', 'counties')])
            + f'<div class="grid" style="grid-template-columns:1fr">{sections}</div>')
    description = f'{op["name"]}: {len(wells):,} North Dakota well records ({active:,} active) across {len(by_county)} counties, with a page for every well.'
    return shell(ctx, title=f'{op["name"]} · wells in North Dakota', description=description, path=path, body=body,
                 crumbs=[('Wells', f'{base}/'), ('Operators', f'{base}/operators/'), (op['name'], None)], current='operators')


def county_page(ctx, county, operators, townships=None):
    base = ctx['base_path']
    path = f'/counties/{county["slug"]}/'
    wells = county['wells']
    active = sum(1 for w in wells if w['status'] == 'A')
    permitted = [w for w in wells if w['status'] == 'LOC']
    recent = sorted((w for w in wells if 'spud' in w['extras']), key=lambda w: w['extras']['spud'], reverse=True)[:100]
    ops = ''.join(f'<li><a href="{base}/operators/{slug}/">{esc(operators[slug]["name"])}</a> <small>{count:,}</small></li>'
                  for slug, count in county['operators'].most_common())
    body = (f'<p class="eyebrow">County</p><h1>{esc(county["name"])}</h1>'
            f'<p class="lede">{len(wells):,} well records in {esc(county["name"])}, North Dakota.</p>'
            + kpis([(f'{len(wells):,}', 'wells'), (f'{active:,}', 'active'), (f'{len(permitted):,}', 'permitted, not drilled'), (f'{len(county["operators"]):,}', 'operators')])
            + f'<div class="cols"><section class="panel"><h2>Operators</h2><ul class="plain">{ops}</ul></section>'
            + tws.county_townships_panel(ctx, county['slug'], townships or {})
            + (f'<section class="panel"><h2>Most recently spud</h2>{well_list(ctx, recent, with_operator=True)}</section>' if recent else '')
            + (f'<section class="panel"><h2>Permitted locations not yet drilled</h2>{well_list(ctx, permitted[:200], with_operator=True)}</section>' if permitted else '')
            + '</div><p class="note">Every well in this county has its own page; use the search or an operator page to reach the rest.</p>')
    description = f'{county["name"]}, North Dakota: {len(wells):,} well records, {active:,} active, {len(permitted):,} permitted locations, with operators and a page for every well.'
    return shell(ctx, title=f'{county["name"]} · wells in North Dakota', description=description, path=path, body=body,
                 crumbs=[('Wells', f'{base}/'), ('Counties', f'{base}/counties/'), (county['name'], None)], current='counties')


def index_pages(ctx, operators, counties, totals):
    base = ctx['base_path']
    op_items = ''.join(f'<li><a href="{base}/operators/{op["slug"]}/">{esc(op["name"])}</a> <small>{len(op["wells"]):,}</small></li>'
                       for op in sorted(operators.values(), key=lambda o: (-len(o['wells']), o['name'])))
    co_items = ''.join(f'<li><a href="{base}/counties/{c["slug"]}/">{esc(c["name"])}</a> <small>{len(c["wells"]):,}</small></li>'
                       for c in sorted(counties.values(), key=lambda c: (-len(c['wells']), c['name'])))
    operators_html = shell(ctx, title='Operators · North Dakota wells', description=f'Every operator in the NDIC wells layer ({len(operators):,}), ranked by well count, each with a page listing its wells.',
                           path='/operators/', body=f'<h1>Operators</h1><p class="lede">{len(operators):,} operator names as published by NDIC, ranked by well count.</p><ul class="plain">{op_items}</ul>',
                           crumbs=[('Wells', f'{base}/'), ('Operators', None)], current='operators')
    counties_html = shell(ctx, title='Counties · North Dakota wells', description=f'North Dakota counties with well records ({len(counties):,}), each with operators, recent spuds and permitted locations.',
                          path='/counties/', body=f'<h1>Counties</h1><p class="lede">Well records by county.</p><ul class="plain">{co_items}</ul>',
                          crumbs=[('Wells', f'{base}/'), ('Counties', None)], current='counties')
    top_ops = ''.join(f'<li><a href="{base}/operators/{op["slug"]}/">{esc(op["name"])}</a> <small>{len(op["wells"]):,}</small></li>'
                      for op in sorted(operators.values(), key=lambda o: (-len(o['wells']), o['name']))[:24])
    search_body = (
        '<p class="eyebrow">North Dakota well index</p><h1>Find any North Dakota well.</h1>'
        f'<p class="lede">Every well record NDIC publishes, {totals["wells"]:,} of them, each with its own page: operator, status, location, map directions and nearby wells.</p>'
        '<form class="search" role="search" onsubmit="return false"><label class="eyebrow" for="q">Search</label>'
        '<input id="q" type="search" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="Well name, API number or NDIC file number" disabled></form>'
        '<p class="hint">Try <code>Hawkinson 1-21H</code>, an API like <code>33-053-04567</code>, or a file number like <code>21994</code>. Spelling does not have to be exact.</p>'
        '<p class="status" id="status" role="status" aria-live="polite">Loading the well index…</p><ol class="results" id="results"></ol>'
        + kpis([(f'{totals["wells"]:,}', 'wells'), (f'{totals["operators"]:,}', 'operators'), (f'{totals["counties"]:,}', 'counties'), (esc(ctx['source_date']), 'NDIC snapshot')])
        + f'<div class="cols"><section class="panel"><h2>Largest operators</h2><ul class="plain">{top_ops}</ul><p class="note"><a href="{base}/operators/">All operators</a></p></section>'
        f'<section class="panel"><h2>Counties</h2><ul class="plain">{co_items}</ul></section></div>'
        + (f'<section class="panel" style="margin-top:16px"><h2>Spacing units and orders</h2>'
           f'<p>{totals["units"]:,} <a href="{base}/units/">drilling spacing units</a> have a page with the unit footprint, the surveyed wells inside it and the NDIC hearing orders that created or changed it; '
           f'{totals["orders"]:,} <a href="{base}/orders/">orders</a> have a page listing the units they touched. Order facts are machine-extracted from the signed orders and not human-reviewed.</p></section>'
           if ctx.get('has_data') else '')
        + '<section class="panel" style="margin-top:16px"><h2>About this index</h2>'
        '<p>The pages are rebuilt automatically from the North Dakota Department of Mineral Resources public GIS download of well records, so they track NDIC\'s own updates, including newly permitted locations before they are drilled. '
        f'Each page carries the published surface location with one-tap directions, and links into the <a href="{MAIN_SITE}/viewer/?dataset=full">regional 3D viewer</a> for wells with surveyed paths.</p>'
        f'<p class="note">Part of <a href="{MAIN_SITE}/">Bakken Subsurface</a>. Not affiliated with NDIC. Read the <a href="{MAIN_SITE}/methodology/">methodology</a>.</p></section>'
        f'<script type="module">import {{mountSearch}} from "{base}/assets/search.js";'
        f'const status=document.getElementById("status");try{{const response=await fetch("{base}/wells/index.json");if(!response.ok)throw new Error("HTTP "+response.status);'
        'mountSearch({input:document.getElementById("q"),results:document.getElementById("results"),status,base:' + json.dumps(base) + ',index:await response.json()});}'
        'catch(error){status.textContent="The search index could not be loaded ("+error.message+"). Operator and county pages still work.";}</script>'
    )
    search_html = shell(ctx, title='Find a North Dakota well · Bakken Subsurface', path='/', body=search_body, current='search',
                        description=f'Search {totals["wells"]:,} North Dakota wells by name, API or NDIC file number. Each well has a page with operator, status, location, map directions and nearby wells.')
    not_found = shell(ctx, title='Page not found · North Dakota well index', description='This well page does not exist.', path='/404.html', robots='noindex',
                      body=f'<h1>No page here.</h1><p class="lede">The well you are looking for may have a different API number. <a href="{base}/">Search the index</a>.</p>')
    return search_html, operators_html, counties_html, not_found


# ----------------------------------------------------------------------------- writing


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def sitemap_urlset(entries):
    body = ''.join(f'<url><loc>{esc(loc)}</loc><lastmod>{lastmod}</lastmod></url>' for loc, lastmod in entries)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n'


def build(source: Path, out: Path, base_url: str, *, limit=None, summary: Path | None = None, data: Path | None = None, generator: str | None = None,
          site_verification: str | None = None, production: Path | None = None):
    started = datetime.now(timezone.utc)
    wells, report = load_sources(source, limit=limit)
    if not wells:
        raise BuildError('No wells read from the source')
    assign_slugs(wells)
    compute_nearby(wells)
    operators = group_operators(wells)
    counties = group_counties(wells)
    base_url = base_url.rstrip('/')
    from urllib.parse import urlsplit
    parts = urlsplit(base_url)
    source_date = layer_date = None
    for archive in report['archives']:
        if archive['kind'] == 'wells':
            source_date = (archive.get('retrievedAt') or '')[:10] or (report.get('provenance') or {}).get('retrievedOn')
            layer_date = http_date(archive.get('lastModified'))   # the date DMR last regenerated the file
    source_date = source_date or started.date().isoformat()
    ctx = {'base_url': base_url, 'base_path': parts.path.rstrip('/'), 'host': parts.netloc,
           'attribution': 'North Dakota Industrial Commission, Department of Mineral Resources, Oil and Gas Division',
           'source_date': source_date, 'built': started.date().isoformat(), 'has_data': False,
           'site_verification': (site_verification or '').strip() or None}
    units, orders, dsu_stats, dsu_data = [], [], {}, None
    if data:
        dsu_data = dsu.load_data(data)
        units, orders, dsu_stats = dsu.attach(wells, dsu_data)
        ctx['has_data'] = True
    prod_data, prod_stats = None, {}
    if production:
        prod_data = prod.load_production(production)
        prod_stats = prod.attach(wells, prod_data)
        ctx['production'] = {'firstMonth': prod_data['months'][0], 'lastMonth': prod_data['months'][-1], 'generated': prod_data.get('generated'),
                             'pdf': (prod_data.get('coverage') or {}).get('pdf')}

    townships = tws.attach(wells, units)

    out = Path(out)
    if out.exists():
        for child in out.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    out.mkdir(parents=True, exist_ok=True)
    (out / 'assets').mkdir()
    shutil.copy(TEMPLATES / 'well.css', out / 'assets' / 'well.css')
    shutil.copy(TEMPLATES / 'search.js', out / 'assets' / 'search.js')
    shutil.copy(TEMPLATES / 'page.js', out / 'assets' / 'page.js')
    shutil.copy(TEMPLATES / 'production.js', out / 'assets' / 'production.js')
    shutil.copy(TEMPLATES / 'compare.js', out / 'assets' / 'compare.js')
    (out / '.nojekyll').write_text('')
    if ctx['host'] and not ctx['host'].endswith('github.io') and not ctx['base_path']:
        (out / 'CNAME').write_text(ctx['host'] + '\n')

    bytes_written = 0
    for w in wells:
        page = well_page(ctx, w)
        write(out / 'wells' / w['slug'] / 'index.html', page)
        bytes_written += len(page.encode('utf-8'))
    for op in operators.values():
        write(out / 'operators' / op['slug'] / 'index.html', operator_page(ctx, op))
    for county in counties.values():
        write(out / 'counties' / county['slug'] / 'index.html', county_page(ctx, county, operators, townships))
    for tw in townships.values():
        write(out / 'townships' / tw['slug'] / 'index.html',
              tws.township_page(ctx, tw, shell=shell, status_text=status_text, well_list=well_list, kpis=kpis, format_date=format_date))
    write(out / 'townships' / 'index.html', tws.townships_index_page(ctx, townships, shell=shell))
    index_paths = []
    if dsu_data:
        for unit in units:
            write(out / 'units' / unit['slug'] / 'index.html', dsu.unit_page(ctx, unit, shell=shell, status_text=status_text))
        for order in orders:
            write(out / 'orders' / order['slug'] / 'index.html', dsu.order_page(ctx, order, shell=shell))
        for path, page in dsu.units_index_pages(ctx, units, dsu_data['counts'], shell=shell).items():
            write(out / path.strip('/') / 'index.html', page)
            index_paths.append(path)
        for path, page in dsu.orders_index_pages(ctx, orders, shell=shell).items():
            write(out / path.strip('/') / 'index.html', page)
            index_paths.append(path)
    totals = {'wells': len(wells), 'operators': len(operators), 'counties': len(counties), 'units': len(units), 'orders': len(orders)}
    search_html, operators_html, counties_html, not_found = index_pages(ctx, operators, counties, totals)
    write(out / 'index.html', search_html)
    write(out / 'wells' / 'index.html', search_html)
    write(out / 'operators' / 'index.html', operators_html)
    write(out / 'counties' / 'index.html', counties_html)
    write(out / '404.html', not_found)

    statuses = sorted({w['status'] for w in wells})
    types = sorted({w['type'] for w in wells})
    op_slugs = sorted(operators, key=lambda s: operators[s]['name'])
    co_slugs = sorted(counties, key=lambda s: counties[s]['name'])
    op_index = {slug: i for i, slug in enumerate(op_slugs)}
    co_index = {slug: i for i, slug in enumerate(co_slugs)}
    status_index = {s: i for i, s in enumerate(statuses)}
    type_index = {t: i for i, t in enumerate(types)}
    rows = []
    for w in wells:
        derived = format_api(w['api']) if w['api'] else ''
        rows.append([w['name'], w['api'], int(w['file']), op_index[w['operator_slug']], co_index[w['county_slug']],
                     status_index[w['status']], type_index[w['type']],
                     round(w['lat'], 5) if w['lat'] is not None else None, round(w['lon'], 5) if w['lon'] is not None else None,
                     None if w['slug'] == derived else w['slug']])
    wells_archive = next((a['file'] for a in report['archives'] if a['kind'] == 'wells'), 'OGD_Wells.zip')
    index = {'generated': started.replace(microsecond=0).isoformat().replace('+00:00', 'Z'), 'source': {'file': wells_archive, 'date': source_date},
             'fields': ['name', 'api', 'file', 'op', 'county', 'status', 'type', 'lat', 'lon', 'slug'],
             'townships': [f'{tw["t"]}-{tw["r"]}' for tw in townships.values()],
             'operators': [operators[s]['name'] for s in op_slugs], 'counties': [counties[s]['name'] for s in co_slugs],
             'statuses': statuses, 'types': types, 'statusGloss': CODES['status'], 'typeGloss': CODES['type'], 'wells': rows}
    write(out / 'wells' / 'index.json', json.dumps(index, ensure_ascii=False, separators=(',', ':')))

    # Sitemap lastmod is the date DMR last regenerated the layer, so a daily rebuild of unchanged
    # data does not tell crawlers that 44,000 pages changed; the retrieval date is the fallback.
    lastmod = layer_date or source_date
    page_urls = [(f'{base_url}/', lastmod), (f'{base_url}/operators/', lastmod), (f'{base_url}/counties/', lastmod)]
    page_urls += [(f'{base_url}/operators/{s}/', lastmod) for s in op_slugs] + [(f'{base_url}/counties/{s}/', lastmod) for s in co_slugs]
    write(out / 'sitemap-pages.xml', sitemap_urlset(page_urls))
    sitemaps = [f'{base_url}/sitemap-pages.xml']
    township_urls = [(f'{base_url}/townships/', lastmod)] + [(f'{base_url}/townships/{tw["slug"]}/', lastmod) for tw in townships.values()]
    write(out / 'sitemap-townships.xml', sitemap_urlset(township_urls))
    sitemaps.append(f'{base_url}/sitemap-townships.xml')
    well_urls = [(f'{base_url}/wells/{w["slug"]}/', lastmod) for w in sorted(wells, key=lambda w: w['slug'])]
    for n in range(0, len(well_urls), WELLS_PER_SITEMAP):
        name = f'sitemap-wells-{n // WELLS_PER_SITEMAP + 1}.xml'
        write(out / name, sitemap_urlset(well_urls[n:n + WELLS_PER_SITEMAP]))
        sitemaps.append(f'{base_url}/{name}')
    if dsu_data:
        data_mod = (dsu_data.get('generated') or '')[:10] or lastmod
        unit_urls = [(f'{base_url}{path}', data_mod) for path in index_paths]
        unit_urls += [(f'{base_url}/units/{u["slug"]}/', data_mod) for u in sorted(units, key=lambda u: u['slug'])]
        unit_urls += [(f'{base_url}/orders/{o["slug"]}/', data_mod) for o in sorted(orders, key=lambda o: o['slug'])]
        for n in range(0, len(unit_urls), WELLS_PER_SITEMAP):
            name = f'sitemap-units-{n // WELLS_PER_SITEMAP + 1}.xml'
            write(out / name, sitemap_urlset(unit_urls[n:n + WELLS_PER_SITEMAP]))
            sitemaps.append(f'{base_url}/{name}')
    write(out / 'sitemap.xml', '<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
          + ''.join(f'<sitemap><loc>{esc(s)}</loc><lastmod>{lastmod}</lastmod></sitemap>' for s in sitemaps) + '</sitemapindex>\n')
    write(out / 'robots.txt', f'User-agent: *\nAllow: /\n\nSitemap: {base_url}/sitemap.xml\n')

    files = [p for p in out.rglob('*') if p.is_file()]
    total_bytes = sum(p.stat().st_size for p in files)
    status_counts = Counter(w['status'] or '—' for w in wells).most_common()
    report.update({
        'generated': index['generated'], 'generator': generator, 'baseUrl': base_url, 'basePath': ctx['base_path'],
        'siteVerification': ctx['site_verification'],
        'sourceDate': source_date, 'layerDate': layer_date, 'lastmod': lastmod,
        'wells': len(wells), 'operators': len(operators), 'counties': len(counties),
        'withCoordinates': sum(1 for w in wells if w['lat'] is not None), 'withoutApi': sum(1 for w in wells if not w['api']),
        'statusCounts': status_counts, 'files': len(files), 'bytes': total_bytes, 'wellPageBytes': bytes_written,
        'indexBytes': (out / 'wells' / 'index.json').stat().st_size, 'sitemaps': len(sitemaps),
        'units': len(units), 'orders': len(orders), 'wellsWithUnits': sum(1 for w in wells if w.get('units')),
        'unitsWithMembers': dsu_stats.get('unitsWithMembers', 0), 'unitMembersNotInLayer': dsu_stats.get('membersNotInLayer', 0),
        'data': ({'file': Path(data).name, 'generated': dsu_data.get('generated'), 'counts': dsu_data.get('counts'), 'sources': dsu_data.get('sources')}
                 if dsu_data else None),
        'wellsWithProduction': prod_stats.get('wellsWithProduction', 0),
        'townships': len(townships), 'sectionsWithWells': sum(1 for tw in townships.values() for s in tw['sections'].values() if s['wells']),
        'wellsWithTownship': sum(1 for w in wells if w.get('township')),
        'production': ({'file': Path(production).name, 'generated': prod_data.get('generated'), 'firstMonth': prod_data['months'][0],
                        'lastMonth': prod_data['months'][-1], 'months': len(prod_data['months']), 'source': (prod_data.get('source') or {}).get('url'),
                        'pdfFirstMonth': (prod.pdf_span(ctx['production']) or (None, None))[0],
                        'pdfLastMonth': (prod.pdf_span(ctx['production']) or (None, None))[1],
                        **prod_stats} if prod_data else None),
        'seconds': round((datetime.now(timezone.utc) - started).total_seconds(), 1),
    })
    write(out / 'build.json', json.dumps(report, indent=2, default=str) + '\n')
    if summary:
        with Path(summary).open('a', encoding='utf-8') as stream:
            stream.write(markdown_summary(report))
    return report


def markdown_summary(report):
    lines = ['## Well pages build', '',
             f'- **{report["wells"]:,} wells**, {report["operators"]:,} operators, {report["counties"]:,} counties → {report["files"]:,} files, {report["bytes"] / 1e6:.1f} MB '
             f'(well pages {report["wellPageBytes"] / 1e6:.1f} MB, search index {report["indexBytes"] / 1e6:.1f} MB) in {report["seconds"]} s',
             f'- Base URL `{report["baseUrl"]}`; NDIC download retrieved {report["sourceDate"]}, DMR file dated {report["layerDate"] or "unknown"} (sitemap lastmod {report["lastmod"]})'
             + (f'; generator `{report["generator"][:12]}`' if report.get('generator') else ''),
             f'- {report["withCoordinates"]:,} wells have coordinates; {report["withoutApi"]:,} lack an API number (their URLs use the file number)']
    if report.get('data'):
        lines += [f'- **{report["units"]:,} spacing units** ({report["unitsWithMembers"]:,} with surveyed wells inside) and **{report["orders"]:,} orders** from `{report["data"]["file"]}` '
                  f'generated {report["data"]["generated"]}; {report["wellsWithUnits"]:,} wells sit in a unit; {report["unitMembersNotInLayer"]:,} unit members were not in the wells layer']
    else:
        lines += ['- No spacing-unit data (`--data` not given): no unit or order pages']
    if report.get('production'):
        p = report['production']
        pdf_text = (f'; months {p["pdfFirstMonth"]} to {p["pdfLastMonth"]} read from the PDF reports, {p.get("unitPoolWells", 0):,} wells '
                    'reported only inside a unit total in those years' if p.get('pdfLastMonth') else '')
        lines += [f'- **Production** for {report["wellsWithProduction"]:,} wells from `{p["file"]}` generated {p["generated"]}: {p["firstMonth"]} to {p["lastMonth"]} '
                  f'({p["months"]} months, {p["feedWells"]:,} wells in the feed, {p["matchedByApi"]:,} matched by API, {p["feedWellsNotInLayer"]:,} not in the wells layer'
                  f'{pdf_text})']
    else:
        lines += ['- No production feed (`--production` not given): no production panels']
    lines += [f'- **{report["townships"]:,} townships** with {report["sectionsWithWells"]:,} sections holding wells; {report["wellsWithTownship"]:,} wells have a parsed legal location']
    lines += ['']
    for archive in report['archives']:
        lines += [f'### {archive["file"]} ({archive["kind"]})', '',
                  f'- {archive["records"]:,} records ({archive["deleted"]} deleted), shape type {archive["shapeType"]}, {"geographic" if archive["geographic"] else "projected"} coordinates',
                  '- Mapped fields: ' + ', '.join(f'{k}←`{v}`' for k, v in archive['mapped'].items()),
                  '- Not mapped (shown only in the attribute table): ' + (', '.join(f'`{f}`' for f in archive['unmapped']) or 'none'),
                  '- Counts: ' + ', '.join(f'{k} {v:,}' for k, v in archive['counts'].items()), '']
    lines += ['### Status codes seen', '', '| Code | Reading | Wells |', '|---|---|---:|']
    lines += [f'| `{code}` | {CODES["status"].get(code, "—")} | {count:,} |' for code, count in report['statusCounts'][:20]]
    if report['warnings']:
        lines += ['', '### Warnings', ''] + [f'- {w}' for w in report['warnings']]
    return '\n'.join(lines) + '\n'


def inspect(path: Path):
    layer = load_layer(path)
    mapping = resolve_fields(layer.field_names)
    inverse = {v: k for k, v in mapping.items()}
    print(f'{path}: layer {layer.name}, {len(layer.rows):,} records ({layer.deleted} deleted), shape type {layer.shape_type}, '
          f'{"geographic" if layer.geographic else "projected or unknown"} coordinates')
    print(f'prj: {layer.prj[:100]}')
    sample = layer.rows[0] if layer.rows else {}
    for f in layer.fields:
        print(f'  {f.name:<14} {f.type} {f.length:>3}.{f.decimals:<2} -> {inverse.get(f.name, "(unmapped)"):<12} e.g. {sample.get(f.name)!r}')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, help='folder from fetch_dmr.py, or one OGD_Wells.zip')
    parser.add_argument('--out', type=Path, help='output folder (emptied first)')
    parser.add_argument('--base-url', default='https://wells.bakkensubsurface.com', help='public origin (and path prefix) the pages will be served from')
    parser.add_argument('--limit', type=int, help='only the first N records (quick local builds)')
    parser.add_argument('--summary', type=Path, help='append a Markdown build summary to this file (GitHub step summary)')
    parser.add_argument('--data', type=Path, help='well-index-data.json.gz from the pipeline: adds spacing-unit and order pages')
    parser.add_argument('--production', type=Path, help='well-production.json.gz from the pipeline: adds the production panel to well pages')
    parser.add_argument('--generator', help='commit the generator runs from, recorded in build.json (GITHUB_SHA)')
    parser.add_argument('--site-verification', default=os.environ.get('GOOGLE_SITE_VERIFICATION'),
                        help='content of the google-site-verification meta tag Search Console asked for (default: $GOOGLE_SITE_VERIFICATION)')
    parser.add_argument('--inspect', type=Path, help='print the fields of a layer and exit')
    args = parser.parse_args(argv)
    if args.inspect:
        return inspect(args.inspect)
    if not args.source or not args.out:
        parser.error('--source and --out are required (or use --inspect)')
    try:
        report = build(args.source, args.out, args.base_url, limit=args.limit, summary=args.summary, data=args.data, generator=args.generator,
                       site_verification=args.site_verification, production=args.production)
    except (BuildError, FileNotFoundError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    print(f'{report["wells"]:,} wells ({report["wellsWithProduction"]:,} with production), {report["units"]:,} units, {report["orders"]:,} orders -> {report["files"]:,} files, {report["bytes"] / 1e6:.1f} MB at {args.out} (base {report["baseUrl"]}) in {report["seconds"]} s')
    for warning in report['warnings']:
        print(f'warning: {warning}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
