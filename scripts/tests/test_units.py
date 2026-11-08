"""Offline tests for the spacing-unit and order pages (scripts/wells/units.py) on the fixture data file."""
import gzip
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells import build_well_pages as bwp  # noqa: E402
from wells import fixture, units  # noqa: E402

NS = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}


class Counter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = {}
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.tags[tag] = self.tags.get(tag, 0) + 1
        if tag == 'a':
            self.links.append(dict(attrs).get('href'))


def parse(html):
    parser = Counter()
    parser.feed(html)
    return parser


class HelperTests(unittest.TestCase):
    def test_event_text(self):
        self.assertEqual(units.event_text({'event': 'spacing', 'verb': 'established', 'wells': 4, 'qualifier': 'stated'}), 'Established the spacing unit, 4 wells allowed')
        self.assertEqual(units.event_text({'event': 'spacing', 'verb': 'redefined', 'wells': 1, 'qualifier': 'stated'}), 'Redefined the spacing unit, 1 well allowed')
        self.assertEqual(units.event_text({'event': 'density', 'verb': None, 'wells': 8.0, 'qualifier': 'up to'}), 'Set the well density, 8 wells allowed (up to)')
        self.assertEqual(units.event_text({'event': 'pooling', 'verb': 'pooled', 'wells': None}), 'Pooled all interests in the unit')
        self.assertEqual(units.event_text({'event': 'termination', 'verb': None, 'wells': None}), 'Terminated the spacing unit')
        self.assertEqual(units.event_text({'event': 'zone', 'verb': None, 'wells': None}), 'Listed the unit in a pool zone table')
        self.assertEqual(units.event_text({'event': 'other', 'verb': 'reinstated', 'wells': None}), 'Reinstated the unit')

    def test_unit_title_and_map_link(self):
        unit = {'id': 'dsu-abcdef1234', 'acres': 1280, 'formation': 'Bakken', 'type': 'SPC', 'lands': ['T152N R104W sections 3, 10'],
                'allowance': {'order': '30000'}, 'events': []}
        self.assertEqual(units.unit_title(unit), ('1,280-acre Bakken spacing unit', 'T152N R104W sections 3, 10'))
        link = units.cases_map_link(unit)
        self.assertEqual(link, 'https://bakkensubsurface.com/cases-and-orders/?order=30000&unit=T152N+R104W+%C2%B7+sections+3%2C+10&zoom=unit#unit-map')
        self.assertEqual(units.unit_title({'id': 'dsu-abcdef1234', 'acres': None, 'formation': None, 'type': 'DRL', 'lands': [], 'dmrOrder': '12345'}),
                         ('Drilling unit', 'Footprint from DMR order 12345'))
        self.assertEqual(units.unit_title({'id': 'dsu-abcdef1234', 'acres': 640.0, 'formation': 'Bakken', 'type': None, 'lands': []})[1], 'Footprint abcdef12')
        self.assertIsNone(units.cases_map_link({'lands': []}))
        self.assertIsNone(units.cases_map_link({'lands': ['T1N R1W sections 1; T2N R1W section 2']}))

    def test_load_data_rejects_other_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.json.gz'
            with gzip.open(path, 'wt', encoding='utf-8') as handle:
                json.dump({'version': 2, 'units': [], 'orders': []}, handle)
            with self.assertRaises(ValueError):
                units.load_data(path)
            plain = Path(tmp) / 'y.json'
            plain.write_text(json.dumps({'version': 1, 'units': [], 'orders': []}), encoding='utf-8')
            self.assertEqual(units.load_data(plain)['units'], [])


class DataBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.source = root / 'dmr'
        cls.out = root / 'site'
        fixture.write_fixture(cls.source, wells=60, permits=False)
        cls.rows = fixture.make_rows(60)
        cls.summary = root / 'summary.md'
        cls.report = bwp.build(cls.source, cls.out, 'https://example.github.io/repo/', summary=cls.summary, data=cls.source / 'well-index-data.json.gz')
        cls.pages = {p.relative_to(cls.out).as_posix(): p.read_text(encoding='utf-8') for p in cls.out.rglob('index.html')}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def slug(self, row):
        return bwp.format_api(row['api'][:10])

    def test_report_counts(self):
        self.assertEqual(self.report['units'], 4)
        self.assertEqual(self.report['orders'], 5)
        self.assertEqual(self.report['unitsWithMembers'], 3)
        self.assertEqual(self.report['unitMembersNotInLayer'], 1)          # the deleted DBF record listed in unit d
        self.assertEqual(self.report['wellsWithUnits'], 4)                  # files 10000, 10001, 10002, 10009
        self.assertEqual(self.report['data']['generated'], '2026-11-07T04:44:11Z')
        self.assertEqual(self.report['data']['counts']['events'], 6)
        summary = self.summary.read_text(encoding='utf-8')
        self.assertIn('**4 spacing units** (3 with surveyed wells inside) and **5 orders**', summary)

    def test_unit_page_is_complete_and_escaped(self):
        html = self.pages['units/dsu-fixture-a/index.html']
        parsed = parse(html)
        self.assertEqual(parsed.tags['h1'], 1)
        self.assertIn('<h1>1,280-acre Bakken spacing unit</h1>', html)
        self.assertIn('T152N R104W sections 3, 10', html)
        self.assertIn('8 wells allowed · order 30000', html)
        self.assertIn('EVIL &amp; CO &quot;QUOTES&quot; LLC', html)
        self.assertNotIn('EVIL & CO', html)
        self.assertIn('Redefined the spacing unit, 8 wells allowed', html)
        self.assertIn('Pooled all interests in the unit', html)
        self.assertIn('machine-extracted', html)
        self.assertIn('Continental Resources', html)                        # headroom holder
        self.assertIn('<dt>Balance under the allowance</dt><dd>+5</dd>', html)
        self.assertIn('<dt>Laterals that still fit (Nov 2026 method)</dt><dd>4 <small>(3.72 two-mile equivalents)</small></dd>', html)
        self.assertIn('<dt>Neighbourhood target density</dt><dd>8.1 two-mile laterals per 1,280 acres</dd>', html)
        self.assertIn('Room to drill: 4 laterals still fit', html)
        self.assertIn('1 more would fit the density but not the spacing rule', html)
        self.assertIn('not a commitment to drill', html)
        self.assertIn('href="https://bakkensubsurface.com/room-to-drill/"', html)
        b = self.pages['units/dsu-fixture-b/index.html']
        self.assertIn('<dt>Laterals that still fit (Nov 2026 method)</dt><dd>0</dd>', b)
        self.assertIn('Room to drill: 0 laterals still fit', b)
        self.assertIn('November 2026 method. The county or basin value stands in for the target', b)   # the fallback note; no blocked-count sentence for this unit
        self.assertNotIn('spacing rule', b)
        self.assertNotIn('county or basin value stands in', self.pages['units/dsu-fixture-a/index.html'])
        self.assertNotIn('Laterals that still fit', self.pages['units/dsu-fixture-c/index.html'])   # not in the feed: nothing said
        for row in self.rows[:3]:
            self.assertIn(f'/repo/wells/{self.slug(row)}/', html)
        self.assertIn('10,400 ft<small>100%</small>', html)
        self.assertEqual(parsed.tags.get('svg'), 1)
        self.assertEqual(parsed.tags.get('circle'), 2)                      # two well heads drawn; OWAN's pad is in another county, off the map
        self.assertIn('1 mi', html)                                         # scale bar
        self.assertIn('href="https://bakkensubsurface.com/viewer/?dataset=full&amp;dsu=dsu-fixture-a"', html)
        self.assertIn('cases-and-orders/?order=30000&amp;unit=T152N+R104W+%C2%B7+sections+3%2C+10&amp;zoom=unit#unit-map', html)
        self.assertIn('/repo/units/dsu-fixture-b/">T152N R104W section 10</a> <small>640-acre Bakken spacing unit</small>', html)   # overlap
        self.assertIn('One other unit shares', html)
        self.assertIn('<link rel="canonical" href="https://example.github.io/repo/units/dsu-fixture-a/">', html)
        self.assertIn('"@type":"Place"', html)
        self.assertIn('"geo":{"@type":"GeoCoordinates"', html)
        self.assertIn('aria-current="page">Spacing units</a>', html)
        self.assertNotIn('inventory', html.lower().split('<main')[1])        # nothing log-top derived leaks into the page
        self.assertNotIn('remainingWells', html)

    def test_units_without_wells_or_orders_and_terminated_units(self):
        c = self.pages['units/dsu-fixture-c/index.html']
        self.assertIn('Footprint from DMR order 12345', c)
        self.assertIn('No surveyed lateral has 660 ft or more inside this footprint', c)
        self.assertIn('No order in the research snapshot has been matched', c)
        self.assertNotIn('<svg', c.split('<h2>Map</h2>')[0])
        self.assertIn('<svg class="unitmap"', c)                            # outline drawn even without wells
        self.assertNotIn('<circle', c)
        self.assertIn('/repo/units/by-county/unknown-county/', c)
        d = self.pages['units/dsu-fixture-d/index.html']
        self.assertIn('Terminated by order 31000 (Nov 15, 2021)', d)        # the termination event, not the allowance order (20000)
        self.assertNotIn('Terminated by order 20000', d)
        self.assertIn('Terminated the spacing unit', d)
        self.assertIn('<dt>Surveyed wells inside</dt><dd>1</dd>', d)       # the deleted record is not counted

    def test_well_pages_show_their_units(self):
        hawkinson = self.pages[f'wells/{self.slug(self.rows[0])}/index.html']
        self.assertIn('<h2>Spacing units</h2>', hawkinson)
        self.assertIn('/repo/units/dsu-fixture-a/', hawkinson)
        self.assertIn('this lateral 10,400 ft inside (100%)', hawkinson)
        self.assertIn('8 wells allowed by order 30000', hawkinson)
        self.assertIn('4 more laterals still fit (Nov 2026 method, machine-made)', hawkinson)
        owan = self.pages[f'wells/{self.slug(self.rows[1])}/index.html']
        self.assertEqual(owan.count('<li data-acres="'), 2)                 # in both units, larger first; acres for compare.js
        self.assertIn('<li data-acres="1280"><a href="/repo/units/dsu-fixture-a/">', owan)
        self.assertLess(owan.index('dsu-fixture-a'), owan.index('dsu-fixture-b'))
        in_d = self.pages[f'wells/{self.slug(self.rows[9])}/index.html']
        self.assertIn('terminated by order 31000 (Nov 15, 2021)', in_d)
        lonely = self.pages[f'wells/{self.slug(self.rows[20])}/index.html']
        self.assertNotIn('<h2>Spacing units</h2>', lonely)

    def test_order_pages(self):
        html = self.pages['orders/18000/index.html']
        parsed = parse(html)
        self.assertEqual(parsed.tags['h1'], 1)
        self.assertIn('<h1>Order 18000</h1>', html)
        self.assertIn('Mar 2, 2010', html)
        self.assertIn('EVIL &amp; CO &quot;QUOTES&quot; LLC', html)
        self.assertIn('<dt>Units touched</dt><dd>2</dd>', html)
        self.assertIn('Established the spacing unit, 4 wells allowed', html)
        self.assertIn('Listed the unit in a pool zone table', html)
        self.assertIn('/repo/units/dsu-fixture-a/', html)
        self.assertIn('/repo/units/dsu-fixture-b/', html)
        self.assertIn('https://www.dmr.nd.gov/oilgas/', html)
        self.assertIn('/repo/orders/by-year/2010/', html)                    # breadcrumb to the year
        self.assertIn('<link rel="canonical" href="https://example.github.io/repo/orders/18000/">', html)

    def test_index_pages(self):
        units_index = self.pages['units/index.html']
        self.assertIn('<h1>Spacing units</h1>', units_index)
        self.assertIn('4 drilling spacing units', units_index)
        self.assertIn('/repo/units/by-county/unknown-county/', units_index)
        county_slug = bwp.slugify(bwp.county_name(self.rows[0]['county']))
        county = self.pages[f'units/by-county/{county_slug}/index.html']
        self.assertIn('/repo/units/dsu-fixture-a/', county)
        self.assertIn('8 allowed · 3 wells', county)
        orders_index = self.pages['orders/index.html']
        self.assertIn('<h1>NDIC orders</h1>', orders_index)
        for year in ('2010', '2012', '2019', '2021'):
            self.assertIn(f'/repo/orders/by-year/{year}/', orders_index)
        year = self.pages['orders/by-year/2010/index.html']
        self.assertIn('/repo/orders/18000/', year)
        self.assertIn('/repo/orders/18001/', year)
        self.assertNotIn('/repo/orders/30000/', year)
        search = self.pages['index.html']
        self.assertIn('<h2>Spacing units and orders</h2>', search)
        self.assertIn('>Spacing units</a>', search)
        self.assertIn('>Orders</a>', search)

    def test_sitemaps_include_units_and_orders(self):
        index = ET.parse(self.out / 'sitemap.xml').getroot()
        maps = [el.text for el in index.findall('s:sitemap/s:loc', NS)]
        self.assertIn('https://example.github.io/repo/sitemap-units-1.xml', maps)
        urls = [el.text for el in ET.parse(self.out / 'sitemap-units-1.xml').getroot().findall('s:url/s:loc', NS)]
        self.assertIn('https://example.github.io/repo/units/', urls)
        self.assertIn('https://example.github.io/repo/orders/', urls)
        self.assertIn('https://example.github.io/repo/units/dsu-fixture-c/', urls)
        self.assertIn('https://example.github.io/repo/orders/31000/', urls)
        self.assertIn('https://example.github.io/repo/orders/by-year/2019/', urls)
        by_county = [u for u in urls if '/units/by-county/' in u]
        self.assertTrue(by_county)
        lastmod = ET.parse(self.out / 'sitemap-units-1.xml').getroot().find('s:url/s:lastmod', NS).text
        self.assertEqual(lastmod, '2026-11-07')                             # the data file's generation date, not the wells snapshot
        # every unit and order page on disk is in the sitemap, and vice versa
        on_disk = {f'https://example.github.io/repo/{p}' for p in self.pages if p.startswith(('units/', 'orders/'))}
        self.assertEqual({u.rstrip('/') + '/index.html' for u in urls}, on_disk)


if __name__ == '__main__':
    unittest.main()
