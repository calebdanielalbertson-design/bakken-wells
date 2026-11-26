"""The machine-readable copy of a well page for bakkensubsurface.com/wells/ (build --layout site).

Each well gets /wells/<slug>/well.json beside its page, served from Workers KV like the page and by the site's API
(bakken-site worker/api.js, GET /api/v1/wells/{api}). It carries what the page shows and nothing more:
the state's record, the published location, the monthly production the page charts, Montana's completion block,
and the nearest wells. Spacing units and hearing orders are left out of the bulk files on purpose.

No build or retrieval date is written here: those change every day and would make every one of the 86,000 files
differ from yesterday's, so each daily publish would rewrite them all. The dates live in /wells/stamp.json, which
the API adds to each response.
"""
from __future__ import annotations

import json

VERSION = 1
SITE = 'https://bakkensubsurface.com'
TERMS = SITE + '/terms/'
UNITS = {'oil': 'bbl', 'water': 'bbl', 'gas': 'mcf', 'gasSold': 'mcf', 'flared': 'mcf', 'days': 'days'}
FACT_KEYS = {'field': 'field', 'pool': 'pool', 'spud': 'spudDate', 'completion': 'completionDate', 'permit_date': 'permitDate',
             'td': 'totalDepthFeet', 'tvd': 'trueVerticalDepthFeet', 'elevation': 'elevationFeet', 'cum_oil': 'cumulativeOilBarrels',
             'cum_gas': 'cumulativeGasMcf', 'cum_water': 'cumulativeWaterBarrels', 'last_prod': 'lastProductionDate'}
NUMERIC = {'td', 'tvd', 'elevation', 'cum_oil', 'cum_gas', 'cum_water'}
ND_PRODUCTION = ('NDIC Monthly Production Reports, as first reported in each month\'s report; operators amend months later and the '
                 'published reports are not updated. A null month is unknown, not zero.')
MT_PRODUCTION = ('Montana Board of Oil and Gas Conservation production file, which carries amended figures; formations reported in one month '
                 'are summed. Gas sold and flared gas are not in the file. A null month is unknown, not zero.')


def _number(value):
    try:
        number = float(str(value).replace(',', ''))
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else number


def _months(first: str, n: int) -> list[str]:
    year, month = int(first[:4]), int(first[5:7])
    out = []
    for _ in range(n):
        out.append(f'{year:04d}-{month:02d}')
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def production_block(w, ctx) -> dict | None:
    entry = w.get('production')
    if not entry:
        return None
    montana = w.get('state') == 'MT'
    feed = ctx.get('montana_production' if montana else 'production') or {}
    series = [s for s in ('oil', 'gas', 'water', 'days') + (() if montana else ('gasSold', 'flared')) if s in entry['v']]
    n = len(entry['v']['oil'])
    return {
        'source': MT_PRODUCTION if montana else ND_PRODUCTION,
        'feedGenerated': feed.get('generated'),
        'firstMonth': entry['first'], 'lastMonth': entry['last'],
        ('formations' if montana else 'pools'): entry.get('pools') or [],
        'units': {s: UNITS[s] for s in series},
        'months': _months(entry['first'], n),
        'series': {s: entry['v'][s] for s in series},
    }


def well_record(w, ctx, *, status_text, type_text) -> dict:
    montana = w.get('state') == 'MT'
    record = {
        'version': VERSION,
        'slug': w['slug'],
        'state': 'MT' if montana else 'ND',
        'api': w.get('api_display') or None,
        'ndicFileNumber': None if montana else (w.get('file') or None),
        'name': w.get('display_name') or w.get('name') or None,
        'operator': w.get('operator') or None,
        'status': w.get('status') or None,
        'statusText': (w.get('status') or None) if montana else status_text(w.get('status')),
        'type': w.get('type') or None,
        'typeText': (w.get('type') or None) if montana else (type_text(w.get('type')) if w.get('type') else None),
        'county': w.get('county') or None,
        'legalLocation': w.get('legal') or None,
        'footages': w.get('footages') or None,
        'location': ({'latitude': round(w['lat'], 6), 'longitude': round(w['lon'], 6), 'datum': 'NAD83', 'note': 'published surface location; a lateral can run miles from it'}
                     if w.get('lat') is not None and w.get('lon') is not None else None),
        'confidential': bool(w.get('confidential')),
        'facts': {FACT_KEYS[k]: (_number(v) if k in NUMERIC else v) for k, v in (w.get('extras') or {}).items() if k in FACT_KEYS},
        'production': production_block(w, ctx),
        'nearby': [{'slug': other['slug'], 'name': other.get('display_name'), 'operator': other.get('operator') or None,
                    'state': other.get('state') or 'ND', 'miles': round(miles, 2)} for miles, other in w.get('nearby') or []],
        'page': f'{ctx["base_url"]}{ctx["well_dir"]}/{w["slug"]}/',
        'source': ('Montana Board of Oil and Gas Conservation, well list and production file' if montana else
                   'North Dakota Industrial Commission, Department of Mineral Resources, Oil and Gas Division: public GIS wells layer'),
        'terms': TERMS,
        'attribution': 'Bakken Subsurface Analytics (bakkensubsurface.com), from public records',
    }
    if montana:
        mt = w.get('mt') or {}
        record['wellFile'] = mt.get('wellFile')
        if mt.get('completion'):
            record['completion'] = mt['completion']
        if mt.get('fracfocus'):
            record['fracfocus'] = mt['fracfocus']
    return record


def well_json(w, ctx, *, status_text, type_text) -> str:
    return json.dumps(well_record(w, ctx, status_text=status_text, type_text=type_text), ensure_ascii=False, separators=(',', ':'))


def stamp(ctx, report, layer_date=None) -> str:
    """/wells/stamp.json: the dates every page and API response shows, kept out of the pages themselves."""
    mt = report.get('montana') or {}
    return json.dumps({'built': ctx['built'], 'wellsRetrieved': ctx['source_date'], 'wellsLayerDate': layer_date,
                       'productionGenerated': (ctx.get('production') or {}).get('generated'),
                       'montanaGenerated': mt.get('generated'), 'montanaRetrieved': (ctx.get('montana_production') or {}).get('retrieved')},
                      separators=(',', ':'))
