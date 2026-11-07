"""Offline tests for the township pages (scripts/wells/townships.py) on the fixture layer and data file."""
import json
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells import build_well_pages as bwp  # noqa: E402
from wells import fixture, townships as tws  # noqa: E402

NS = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}


class HelperTests(unittest.TestCase):
    def test_parse_legal_and_lands(self):
        self.assertEqual(tws.parse_legal('NWNW Sec 28 T153N R91W'), {'qq': 'NWNW', 'section': 28, 'township': 153, 'range': 91})
        self.assertEqual(tws.parse_legal('C NW Sec 21 T157N R95W')['qq'], 'C NW')
        self.assertEqual(tws.parse_legal('Sec 2 T140N R73W')['qq'], '')
        self.assertIsNone(tws.parse_legal('Sec 37 T140N R73W'))
        self.assertIsNone(tws.parse_legal(''))
        self.assertIsNone(tws.parse_legal(None))
        self.assertEqual(tws.parse_lands(['T155N R103W sections 15, 22']), [(155, 103, [15, 22])])
        self.assertEqual(tws.parse_lands(['T153N R95W section 27', 'T153N R95W sections 34, 35; T153N R96W section 1']),
                         [(153, 95, [27]), (153, 95, [34, 35]), (153, 96, [1])])
        self.assertEqual(tws.parse_lands(None), [])
        self.assertEqual((tws.slug_of(153, 91), tws.short_name(153, 91), tws.long_name(153, 91)),
                         ('153n-91w', 'T153N R91W', 'Township 153 North, Range 91 West'))

    def test_grid_and_quarter_quarter_positions(self):
        self.assertEqual(tws.CELL[1], (5, 0))      # section 1 is the north-east corner
        self.assertEqual(tws.CELL[6], (0, 0))
        self.assertEqual(tws.CELL[7], (0, 1))      # the numbering snakes
        self.assertEqual(tws.CELL[36], (5, 5))
        self.assertEqual(len(tws.CELL), 36)
        self.assertEqual(tws.qq_position('NWNW'), (0.125, 0.125, False))
        self.assertEqual(tws.qq_position('SESE'), (0.875, 0.875, False))
        self.assertEqual(tws.qq_position('NWNE'), (0.625, 0.125, False))   # NW quarter-quarter of the NE quarter
        self.assertEqual(tws.qq_position('C NW'), (0.25, 0.25, False))
        self.assertEqual(tws.qq_position('E2NE'), (0.875, 0.25, False))
        self.assertEqual(tws.qq_position('LOT4'), (0.5, 0.5, True))
        self.assertEqual(tws.qq_position(''), (0.5, 0.5, True))


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.source, cls.out = root / 'source', root / 'site'
        fixture.write_fixture(cls.source, wells=60, permits=False)
        cls.report = bwp.build(cls.source, cls.out, 'https://example.github.io/repo/', data=cls.source / 'well-index-data.json.gz')
        cls.build_json = json.loads((cls.out / 'build.json').read_text(encoding='utf-8'))
        cls.rows = fixture.make_rows(60)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_counts_and_sitemap(self):
        self.assertGreater(self.report['townships'], 10)
        self.assertEqual(self.report['wellsWithTownship'], self.report['wells'])
        self.assertGreaterEqual(self.report['sectionsWithWells'], self.report['townships'])
        self.assertIn('townships**', bwp.markdown_summary(self.report))
        sitemap = ET.parse(self.out / 'sitemap-townships.xml').getroot()
        locs = [u.find('s:loc', NS).text for u in sitemap.findall('s:url', NS)]
        self.assertEqual(locs[0], 'https://example.github.io/repo/townships/')
        self.assertEqual(len(locs), self.report['townships'] + 1)
        index = ET.parse(self.out / 'sitemap.xml').getroot()
        self.assertIn('https://example.github.io/repo/sitemap-townships.xml', [s.find('s:loc', NS).text for s in index.findall('s:sitemap', NS)])
        search = json.loads((self.out / 'wells' / 'index.json').read_text(encoding='utf-8'))
        self.assertEqual(len(search['townships']), self.report['townships'])
        self.assertTrue(all(re.fullmatch(r'\d+-\d+', key) for key in search['townships']))

    def test_township_page_places_every_well_and_links_units(self):
        row = self.rows[0]
        slug = f'{row["township"]}n-{row["range"]}w'
        html = (self.out / 'townships' / slug / 'index.html').read_text(encoding='utf-8')
        self.assertIn(f'<h1>Township {row["township"]} North, Range {row["range"]} West</h1>', html)
        self.assertIn('<svg class="twpgrid"', html)
        self.assertEqual(html.count('<rect class="sec'), 36)
        self.assertIn(f'<h3 id="section-{row["section"]}">Section {row["section"]}</h3>', html)
        self.assertIn('HAWKINSON 1-21H', html)
        api = row['api']
        self.assertIn(f'/repo/wells/{api[:2]}-{api[2:5]}-{api[5:10]}/', html)
        # every well of the township is a dot inside the grid
        dots = html.count('<circle class="')
        wells_here = sum(1 for r in self.rows if not r.get('_deleted') and r['township'] == row['township'] and r['range'] == row['range'])
        self.assertEqual(dots, wells_here)
        self.assertIn('Township and range are north and west throughout North Dakota', html)
        # the unit whose land description names this township appears, with its sections
        unit_township = (self.out / 'townships' / '152n-104w' / 'index.html')
        self.assertTrue(unit_township.exists(), 'fixture unit a names T152N R104W')
        unit_html = unit_township.read_text(encoding='utf-8')
        self.assertIn('/repo/units/dsu-fixture-a/', unit_html)
        self.assertIn('sections 3, 10', unit_html)
        self.assertIn('<rect class="sec unit', unit_html)
        self.assertIn('<h2>Orders</h2>', unit_html)
        self.assertIn('/repo/orders/30000/', unit_html)

    def test_index_county_and_well_pages_link_townships(self):
        index_html = (self.out / 'townships' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('<h1>Townships</h1>', index_html)
        self.assertEqual(len(re.findall(r'href="/repo/townships/\d+n-\d+w/"', index_html)), self.report['townships'])
        row = self.rows[0]
        api = row['api']
        well_html = (self.out / 'wells' / f'{api[:2]}-{api[2:5]}-{api[5:10]}' / 'index.html').read_text(encoding='utf-8')
        self.assertIn(f'<dt>Legal location</dt><dd><a href="/repo/townships/{row["township"]}n-{row["range"]}w/#section-{row["section"]}">', well_html)
        self.assertIn('<a href="/repo/townships/">Townships</a>', well_html)
        county_html = next((self.out / 'counties').glob('*/index.html')).read_text(encoding='utf-8')
        self.assertIn('<h2>Townships</h2>', county_html)

    def test_malformed_legal_locations_are_left_unlinked(self):
        ctx = {'base_path': '/repo'}
        self.assertEqual(tws.legal_link(ctx, {'legal': 'Somewhere odd'}), 'Somewhere odd')
        townships = tws.attach([{'legal': 'Somewhere odd', 'file': '1', 'display_name': 'X', 'status': 'A', 'county': None, 'operator': None, 'slug': 'x'}], [])
        self.assertEqual(townships, {})


if __name__ == '__main__':
    unittest.main()
