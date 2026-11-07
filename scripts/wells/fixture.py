"""Synthetic OGD_Wells-shaped archives for tests and local previews (no DMR download needed).

    python scripts/wells/fixture.py --out work/dmr-fixture --wells 1500

Writes OGD_Wells.zip (and an optional permits archive) plus download-provenance.json in the same
shape fetch_dmr.py produces, using the field names confirmed in the real layer (fileno, api,
well_name, operator, status) and plausible extras. Names, operators and numbers are invented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import tempfile
import zipfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells.shapefile import Field, write_layer  # noqa: E402

NAD83_PRJ = 'GEOGCS["GCS_North_American_1983",DATUM["D_North_American_1983",SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
FIELDS = [Field('fileno', 'N', 10, 0), Field('api', 'C', 14, 0), Field('well_name', 'C', 80, 0), Field('operator', 'C', 100, 0),
          Field('status', 'C', 6, 0), Field('well_type', 'C', 6, 0), Field('county', 'C', 20, 0), Field('field_name', 'C', 40, 0),
          Field('spud_date', 'D', 8, 0), Field('td', 'N', 10, 0), Field('lat', 'N', 12, 6), Field('long', 'N', 12, 6),
          Field('qq', 'C', 10, 0), Field('section', 'N', 3, 0), Field('township', 'N', 4, 0), Field('range', 'N', 4, 0),
          Field('cum_oil', 'N', 12, 0), Field('tight', 'L', 1, 0), Field('misc_code', 'C', 12, 0),
          Field('api_no', 'C', 25, 0), Field('feet_ns', 'N', 10, 0), Field('fnsl', 'C', 1, 0), Field('feet_ew', 'N', 10, 0), Field('fewl', 'C', 1, 0), Field('symbol', 'C', 50, 0)]
COUNTIES = {'MCKENZIE': (53, 47.75, -103.4), 'WILLIAMS': (105, 48.3, -103.5), 'MOUNTRAIL': (61, 48.2, -102.4), 'DUNN': (25, 47.4, -102.6),
            'DIVIDE': (23, 48.8, -103.5), 'BURKE': (13, 48.8, -102.5), 'STARK': (89, 46.8, -102.7), 'BILLINGS': (7, 47.0, -103.4)}
OPERATORS = ['CONTINENTAL RESOURCES, INC.', 'HESS BAKKEN INVESTMENTS II, LLC', 'CHORD ENERGY CORPORATION', 'DEVON ENERGY PRODUCTION COMPANY, L.P.',
             'MARATHON OIL COMPANY', 'KRAKEN OPERATING, LLC', 'XTO ENERGY INC.', 'WHITING OIL AND GAS CORPORATION', 'GRAYSON MILL OPERATING, LLC',
             'PETRO-HUNT, L.L.C.', 'SLAWSON EXPLORATION COMPANY, INC.', 'ZAVANNA, LLC']
LEASES = ['HAWKINSON', 'OWAN', 'NYSTUEN', 'DURANT', 'HALEY', 'NYGAARD', 'ORTLOFF', 'CLOVER', 'ROADRUNNER', 'BRATCHER', 'SORENSON', 'JOHNSRUD',
          'ANDERSON', 'OLSON', 'LARSON', 'NELSON', 'HANSON', 'JOHNSON', 'ERICKSON', 'MOBERG', 'BANG', 'KOALA', 'MAUS', 'PATTERSON', 'ROLFSRUD']
STATUSES = ['A'] * 55 + ['PA'] * 20 + ['IA'] * 8 + ['LOC'] * 6 + ['DRL'] * 3 + ['TA'] * 3 + ['DRY'] * 3 + ['PNC'] * 2
TYPES = ['OG'] * 90 + ['SWD'] * 6 + ['WI'] * 2 + ['DRY'] * 2


def make_rows(count: int, seed: int = 7):
    rng = random.Random(seed)
    rows, api_seen = [], set()
    for i in range(count):
        county, (code, lat0, lon0) = rng.choice(list(COUNTIES.items()))
        serial = rng.randint(1, 99999)
        api = f'33{code:03d}{serial:05d}0000'
        while api in api_seen:
            serial = rng.randint(1, 99999)
            api = f'33{code:03d}{serial:05d}0000'
        api_seen.add(api)
        spud = date(2006, 1, 1) + timedelta(days=rng.randint(0, 7300))
        status = rng.choice(STATUSES)
        rows.append({'fileno': 10000 + i, 'api': api,
                     'well_name': f'{rng.choice(LEASES)} {rng.randint(1, 44)}-{rng.randint(1, 36)}{rng.choice(["H", "H", "TFH", "MBH", ""])}',
                     'operator': rng.choice(OPERATORS), 'status': status, 'well_type': rng.choice(TYPES), 'county': county,
                     'field_name': rng.choice(['BAILEY', 'SANISH', 'PARSHALL', 'BANKS', 'ELM TREE', 'SIVERSTON', 'TRUAX']),
                     'spud_date': None if status in ('LOC', 'PNC') else spud.isoformat(), 'td': None if status in ('LOC', 'PNC') else rng.randint(9000, 22000),
                     'lat': round(lat0 + rng.uniform(-0.25, 0.25), 6), 'long': round(lon0 + rng.uniform(-0.35, 0.35), 6),
                     'qq': rng.choice(['NWNW', 'NENE', 'SWSW', 'SESE', 'LOT1']), 'section': rng.randint(1, 36), 'township': rng.randint(140, 164), 'range': rng.randint(90, 104),
                     'cum_oil': None if status in ('LOC', 'PNC', 'DRY') else rng.randint(1000, 900000), 'tight': rng.random() < 0.04,
                     'misc_code': rng.choice(['', 'X1', 'R2']),
                     'api_no': f'{api[:2]}-{api[2:5]}-{api[5:10]}-{api[10:12]}-{api[12:14]}', 'feet_ns': rng.randint(150, 2500), 'fnsl': rng.choice(['N', 'S']),
                     'feet_ew': rng.randint(150, 2500), 'fewl': rng.choice(['E', 'W']), 'symbol': None})
        rows[-1]['symbol'] = f"{rows[-1]['status']}-{rows[-1]['well_type']}"
    # Edge cases the builder must handle.
    rows[0]['well_name'] = 'HAWKINSON 1-21H'
    rows[0]['status'] = 'A'
    rows[1].update({'well_name': 'OWAN 3-14H', 'status': 'A'})
    rows[2].update({'well_name': '<script>alert(1)</script> 1H', 'operator': 'EVIL & CO "QUOTES" LLC',
                    'county': rows[0]['county'], 'lat': rows[0]['lat'] + 0.0003, 'long': rows[0]['long'] + 0.0003})  # same pad as row 0
    rows[3].update({'api': rows[0]['api']})                     # duplicate API, different file number -> slug suffix
    rows[4].update({'api': '', 'api_no': ''})                   # no API -> file-based slug
    rows[5].update({'lat': 12.0, 'long': 12.0})                 # outside North Dakota -> coordinates rejected, falls back to SHP point (also bad)
    rows[6].update({'well_name': '', 'status': ''})             # unnamed, no status
    rows[7].update({'_deleted': True})                          # deleted DBF record -> skipped
    for row in rows:
        row['_x'], row['_y'] = row['long'], row['lat']
    return rows


# The real OGD_PermitStatusBeforeSpud layer carries: api, operator, dayrange, timesrenew, lat, lon, fileno.
PERMIT_FIELDS = [Field('api', 'C', 14, 0), Field('operator', 'C', 100, 0), Field('dayrange', 'C', 7, 0), Field('timesrenew', 'N', 5, 0),
                 Field('lat', 'F', 19, 11), Field('lon', 'F', 19, 11), Field('fileno', 'N', 10, 0)]


def permit_rows(base_rows):
    first = base_rows[8]
    return [
        {'fileno': first['fileno'], 'api': first['api'], 'operator': first['operator'], 'dayrange': '0', 'timesrenew': 15,
         'lat': first['lat'], 'lon': first['long'], '_x': first['long'], '_y': first['lat']},
        {'fileno': 90001, 'api': '33053099990000', 'operator': 'CONTINENTAL RESOURCES, INC.', 'dayrange': '31-60', 'timesrenew': 0,
         'lat': 47.81, 'lon': -103.21, '_x': -103.21, '_y': 47.81},
        {'fileno': 90002, 'api': '33105099980000', 'operator': 'HESS BAKKEN INVESTMENTS II, LLC', 'dayrange': '0', 'timesrenew': 2,
         'lat': 48.31, 'lon': -103.51, '_x': -103.51, '_y': 48.31},
    ]


def zip_layer(folder: Path, name: str, fields, rows, prj=NAD83_PRJ) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        write_layer(Path(tmp), name, fields, rows, prj)
        archive = folder / f'{name}.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as out:
            for ext in ('.dbf', '.shp', '.prj'):
                out.write(Path(tmp) / f'{name}{ext}', f'{name}{ext}')
    return archive


def write_fixture(out: Path, wells: int = 300, permits: bool = True, seed: int = 7) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows = make_rows(wells, seed)
    archives = [('wells', zip_layer(out, 'OGD_Wells', FIELDS, rows))]
    if permits:
        archives.append(('permits', zip_layer(out, 'OGD_PermitStatusBeforeSpud', PERMIT_FIELDS, permit_rows(rows))))
    provenance = {'downloadPage': 'fixture', 'retrievedOn': '2026-10-17', 'archives': [
        {'name': kind, 'kind': kind, 'file': path.name, 'url': f'fixture://{path.name}', 'bytes': path.stat().st_size,
         'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'lastModified': None, 'retrievedAt': '2026-10-17T12:00:00Z'}
        for kind, path in archives]}
    (out / 'download-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    return provenance


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--wells', type=int, default=300)
    parser.add_argument('--no-permits', action='store_true')
    parser.add_argument('--seed', type=int, default=7)
    args = parser.parse_args(argv)
    provenance = write_fixture(args.out, args.wells, not args.no_permits, args.seed)
    for archive in provenance['archives']:
        print(f'{archive["file"]}: {archive["bytes"]:,} bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main())
