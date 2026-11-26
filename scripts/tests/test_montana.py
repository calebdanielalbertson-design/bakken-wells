"""Offline tests for Montana wells on bakkensubsurface.com/wells/ (scripts/wells/montana.py, build --layout site --montana)."""
from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells import build_well_pages as bwp  # noqa: E402
from wells import fixture  # noqa: E402
from wells import montana as mtw  # noqa: E402

MONTHS = 4


def montana_fixture(path: Path):
    """Three wells: a producing Bakken horizontal with a completion report and FracFocus water, a plugged dry hole south
    of the baseline, and a permit with no township."""
    data = {
        'version': 1, 'kind': 'montana-well-index', 'state': 'MT', 'built': '2026-11-25', 'generated': '2026-11-25T12:00:00Z',
        'source': {'title': 'Montana Board of Oil and Gas Conservation, public files', 'retrieved': '2026-11-19'},
        'production': {'firstMonth': '2026-05', 'lastMonth': '2026-08', 'months': MONTHS, 'note': 'test'},
        'completions': {'note': 'Read by machine from the completion report.', 'kindNote': 'String names follow North Dakota\'s rule.',
                        'fracfocusNote': 'Frac water as the operator disclosed it.'},
        'counts': {'wells': 3},
        'wells': [
            {'api': '2508322812', 'api14': '25083228120000', 'name': 'Vandal 1-16H', 'operator': 'Swanson Oil & Gas LLC', 'status': 'Producing',
             'type': 'Oil', 'slant': 'Horizontal', 'county': 'Richland', 'twp': 23, 'twpDir': 'N', 'rng': 53, 'rngDir': 'E', 'sec': 16,
             'qtr': 'NE NW', 'field': 'Elm Coulee', 'spudded': '2010-01-11', 'td': 14310, 'lat': 47.6, 'lon': -104.4,
             'wellFile': 'https://bogfiles.dnrc.mt.gov/Well_Data/2508322812/2508322812.pdf',
             'production': {'api': '2508322812', 'pools': ['Bakken'], 'first': '2026-05', 'last': '2026-08', 'reported': 3,
                            'v': {'oil': [900, None, 800, 0], 'water': [300, None, 280, 0], 'gas': [500, None, 450, 0], 'days': [31, None, 31, 0]}},
             'completion': {'page': 2, 'reports': 1, 'completed': '2010-04-15',
                            'casing': [{'kind': 'surface', 'size': 9.625, 'top': None, 'bottom': 1454, 'weight': 36.0, 'grade': 'K-55'}],
                            'intervals': [{'top': 10671, 'bottom': 14308, 'openHole': False}], 'stages': None, 'sand': 30000, 'water': None,
                            'test': {'oil': 93.0, 'gas': 50.0, 'water': 2.0, 'hours': 24.0}},
             'fracfocus': {'waterGallons': 618510, 'jobStart': '2012-03-09', 'disclosures': 1}},
            {'api': '2500105000', 'api14': '25001050000000', 'name': 'Benj. C. Crowley 1', 'operator': 'Hagenbarth', 'status': 'P&A - Approved',
             'type': 'Dry Hole', 'slant': 'Vertical', 'county': 'Beaverhead', 'twp': 7, 'twpDir': 'S', 'rng': 10, 'rngDir': 'W', 'sec': 2,
             'qtr': 'SE NW NW', 'td': 812, 'lat': 45.25851, 'lon': -112.7998},
            {'api': '2508399999', 'api14': '25083999990000', 'name': 'New Permit 1H', 'operator': 'Swanson Oil & Gas LLC', 'status': 'Permit to Drill',
             'type': 'Oil', 'county': 'Richland'},
        ],
    }
    with gzip.open(path, 'wt', encoding='utf-8') as handle:
        json.dump(data, handle)


class MontanaPagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        fixture.write_fixture(root / 'dmr', wells=60)
        montana_fixture(root / 'mt.json.gz')
        cls.out = root / 'site'
        cls.report = bwp.build(root / 'dmr', cls.out, 'https://bakkensubsurface.com/wells', layout='site', montana=root / 'mt.json.gz',
                               data=root / 'dmr' / 'well-index-data.json.gz', production=root / 'dmr' / 'well-production.json.gz')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def page(self, rel):
        return (self.out / rel / 'index.html').read_text(encoding='utf-8')

    def test_a_montana_well_page(self):
        html = self.page('25-083-22812')
        self.assertIn('<link rel="canonical" href="https://bakkensubsurface.com/wells/25-083-22812/">', html)
        self.assertIn('Montana · API 25-083-22812-00-00', html)
        self.assertIn('href="/wells/counties/mt-richland/"', html)
        self.assertIn('href="/wells/townships/mt-23n-53e/#section-16"', html)
        self.assertIn('Montana Board of Oil and Gas Conservation production file', html)
        self.assertIn('The file carries amended figures', html)
        self.assertNotIn('NDIC Monthly Production Reports', html)
        tile = '<span class="l">Cumulative gas</span>'
        self.assertIn(tile, html)                                   # the Board's file has no gas sold: no empty share tile
        self.assertNotIn('Gas sold, last 12 months', html)
        self.assertIn('<small>Oil, water, gas, days, and the rate and ratios</small>', html)
        nd = self.page(next(p.name for p in self.out.iterdir() if p.name.startswith('33-') and 'Gas sold, last 12 months' in self.page(p.name)))
        self.assertNotIn(tile, nd)
        self.assertIn('<h2>Completion report</h2>', html)
        self.assertIn('9⅝ in, 36.0 lb/ft, K-55', html)
        self.assertIn('10,671 – 14,308 ft', html)
        self.assertIn('618,510 gal', html)
        self.assertIn('/viewer/?dataset=full&amp;well=2508322812', html)
        self.assertIn('Board\'s scanned well file (PDF)', html)
        self.assertNotIn('NDIC file', html)
        self.assertEqual(html.count('<!--site-header-->'), 1)

    def test_outside_the_basin_and_south_of_the_baseline(self):
        html = self.page('25-001-05000')
        self.assertNotIn('well=2500105000', html)          # Beaverhead is not in the viewer's Montana layer
        self.assertIn('href="/wells/townships/mt-7s-10w/#section-2"', html)
        self.assertIn('class="badge off"', html)
        township = self.page('townships/mt-7s-10w')
        self.assertIn('Township 7 South, Range 10 West', township)
        self.assertIn('Montana townships run north or south', township)
        permit = self.page('25-083-99999')
        self.assertIn('has no production rows', permit)
        self.assertNotIn('<h2>Completion report</h2>', permit)

    def test_counties_operators_and_search(self):
        county = self.page('counties/mt-richland')
        self.assertIn('well records in Richland, Montana', county)
        operator = (self.out / 'operators' / 'swanson-oil-gas-llc' / 'index.html').read_text(encoding='utf-8')
        self.assertIn("the Montana Board's well list", operator)
        self.assertIn('1</b><span>permitted, not drilled', operator)
        index = json.loads((self.out / 'index.json').read_text(encoding='utf-8'))
        montana = [row for row in index['wells'] if row[1].startswith('25')]
        self.assertEqual(len(montana), 3)
        self.assertTrue(all(row[2] is None for row in montana))
        self.assertEqual(index['countyStates'][montana[0][4]], 'MT')
        self.assertTrue(all('-' in t and not t.startswith('7-') for t in index['townships']))   # North Dakota's only
        search = (self.out / 'index.html').read_text(encoding='utf-8')
        self.assertIn('Find any well in North Dakota or Montana.', search)
        self.assertEqual(self.report['montana']['wells'], 3)

    def test_north_dakota_pages_keep_their_wording(self):
        nd = next(p for p in self.out.iterdir() if p.name.startswith('33-'))
        html = (nd / 'index.html').read_text(encoding='utf-8')
        self.assertIn('NDIC file', html)
        self.assertIn('/wells/counties/nd-', html)

    def test_montana_needs_the_site_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture.write_fixture(root / 'dmr', wells=60)
            montana_fixture(root / 'mt.json.gz')
            with self.assertRaises(bwp.BuildError):
                bwp.build(root / 'dmr', root / 'out', 'https://example.github.io/repo', montana=root / 'mt.json.gz')

    def test_helpers(self):
        self.assertEqual(mtw.pipe(9.625), '9⅝')
        self.assertEqual(mtw.pipe(5.5), '5½')
        self.assertEqual(mtw.pipe(7.0), '7')
        self.assertEqual(mtw.legal_text({'twp': 23, 'twpDir': 'N', 'rng': 53, 'rngDir': 'E', 'sec': 16, 'qtr': 'NE NW'}), 'NE NW Sec 16 T23N R53E')
        self.assertEqual(mtw.legal_text({'county': 'Richland'}), '')


if __name__ == '__main__':
    unittest.main()
