"""Offline tests for the well-pages pipeline (scripts/wells): shapefile reader, builder, fixtures."""
import json
import math
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells import build_well_pages as bwp  # noqa: E402
from wells import fixture, shapefile  # noqa: E402


class WellCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.h1 = 0
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'h1':
            self.h1 += 1
        if tag == 'a':
            self.links.append(dict(attrs).get('href'))


class ShapefileTests(unittest.TestCase):
    def test_roundtrip_including_deleted_and_null_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fields = [shapefile.Field('fileno', 'N', 10, 0), shapefile.Field('api', 'C', 14, 0), shapefile.Field('spud_date', 'D', 8, 0),
                      shapefile.Field('lat', 'N', 12, 6), shapefile.Field('tight', 'L', 1, 0)]
            rows = [{'fileno': 1, 'api': '33053000010000', 'spud_date': '2020-01-15', 'lat': 47.123456, 'tight': True, '_x': -103.1, '_y': 47.123456},
                    {'fileno': 2, 'api': '', 'spud_date': None, 'lat': None, 'tight': False, '_deleted': True, '_x': -103.2, '_y': 47.2},
                    {'fileno': 3, 'api': 'x', 'spud_date': '2021-12-31', 'lat': 48.0, 'tight': None}]
            shp = shapefile.write_layer(Path(tmp), 'layer', fields, rows, fixture.NAD83_PRJ)
            layer = shapefile.load_layer(shp)
            self.assertEqual(layer.field_names, ['fileno', 'api', 'spud_date', 'lat', 'tight'])
            self.assertEqual(layer.deleted, 1)
            self.assertEqual([r['fileno'] for r in layer.rows], [1, 3])
            self.assertEqual(layer.rows[0]['spud_date'], '2020-01-15')
            self.assertAlmostEqual(layer.rows[0]['lat'], 47.123456)
            self.assertIs(layer.rows[0]['tight'], True)
            self.assertEqual((layer.rows[0]['_x'], layer.rows[0]['_y']), (-103.1, 47.123456))
            self.assertEqual((layer.rows[1]['_x'], layer.rows[1]['_y']), (None, None))  # null shape
            self.assertTrue(layer.geographic)
            self.assertEqual(layer.shape_type, 1)

    def test_zip_member_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            fixture.write_fixture(out, wells=12, permits=False)
            layer = shapefile.load_layer(out / 'OGD_Wells.zip')
            self.assertEqual(layer.name, 'OGD_Wells')
            self.assertEqual(len(layer.rows), 11)  # one deleted record in the fixture
            self.assertIn('well_name', layer.field_names)


class HelperTests(unittest.TestCase):
    def test_field_resolution_prefers_confirmed_names(self):
        mapping = bwp.resolve_fields(['fileno', 'api', 'well_name', 'operator', 'status', 'long', 'lat', 'misc'])
        self.assertEqual(mapping['file'], 'fileno')
        self.assertEqual(mapping['name'], 'well_name')
        self.assertEqual(mapping['lon'], 'long')
        self.assertNotIn('county', mapping)

    def test_formatting(self):
        self.assertEqual(bwp.format_api('3305304567'), '33-053-04567')
        self.assertEqual(bwp.format_api('33053045670000'), '33-053-04567-0000')
        self.assertEqual(bwp.parse_date('1/5/2019'), '2019-01-05')
        self.assertEqual(bwp.parse_date('20190105'), '2019-01-05')
        self.assertIsNone(bwp.parse_date('n/a'))
        self.assertEqual(bwp.format_date('2019-01-05'), 'Jan 5, 2019')
        self.assertEqual(bwp.county_name('MCKENZIE'), 'McKenzie')
        self.assertEqual(bwp.county_name('golden valley'), 'Golden Valley')
        self.assertEqual(bwp.slugify('Continental Resources, Inc.'), 'continental-resources-inc')
        self.assertTrue(bwp.plausible(47.8, -103.2))
        self.assertFalse(bwp.plausible(12.0, 12.0))


class NearbyTests(unittest.TestCase):
    def test_wells_up_to_the_radius_are_found_across_grid_cells(self):
        from wells import build_well_pages as b
        # 2.9 miles due north and due east of the first well: both must be found, the 3.2-mile one must not.
        base = {'lat': 47.8, 'lon': -103.2}
        north = 2.9 / 69.0
        east = 2.9 / (69.0 * math.cos(math.radians(47.8)))
        far = 3.2 / 69.0
        wells = [dict(file='1', lat=base['lat'], lon=base['lon']), dict(file='2', lat=base['lat'] + north, lon=base['lon']),
                 dict(file='3', lat=base['lat'], lon=base['lon'] + east), dict(file='4', lat=base['lat'] + far, lon=base['lon'])]
        b.compute_nearby(wells)
        found = sorted(other['file'] for _, other in wells[0]['nearby'])
        self.assertEqual(found, ['2', '3'])
        self.assertTrue(all(miles <= b.NEARBY_MILES for miles, _ in wells[0]['nearby']))


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.source = root / 'dmr'
        cls.out = root / 'site'
        fixture.write_fixture(cls.source, wells=120, permits=True)
        cls.summary = root / 'summary.md'
        cls.report = bwp.build(cls.source, cls.out, 'https://example.github.io/repo/', summary=cls.summary)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_counts_and_report(self):
        # 120 fixture rows, one deleted, plus two new permitted locations from the permit layer
        self.assertEqual(self.report['wells'], 121)
        self.assertEqual(self.report['basePath'], '/repo')
        self.assertEqual(self.report['baseUrl'], 'https://example.github.io/repo')
        wells_archive = next(a for a in self.report['archives'] if a['kind'] == 'wells')
        self.assertEqual(wells_archive['mapped']['name'], 'well_name')
        self.assertEqual(wells_archive['unmapped'], ['misc_code', 'symbol'])
        self.assertEqual(wells_archive['counts']['duplicate_file_number'], 0)
        permits = next(a for a in self.report['archives'] if a['kind'] == 'permits')
        self.assertEqual(permits['counts']['wells'], 2)                 # two permitted locations OGD_Wells lacked
        self.assertEqual(permits['counts']['duplicate_file_number'], 1)
        self.assertEqual(permits['counts']['enriched'], 1)              # the one already present gained the layer's columns
        self.assertEqual(permits['unmapped'], ['dayrange', 'timesrenew'])
        self.assertEqual(self.report['warnings'], [])                  # a supplementary layer without name/status is not a warning
        enriched = [p for p in (self.out / 'wells').glob('*/index.html') if 'permits: timesrenew' in p.read_text(encoding='utf-8')]
        self.assertEqual(len(enriched), 1)
        self.assertIn('permit-status layer', enriched[0].read_text(encoding='utf-8'))
        self.assertEqual(self.report['sourceDate'], '2026-10-17')
        self.assertIn('## Well pages build', self.summary.read_text(encoding='utf-8'))

    def test_pages_exist_with_expected_slugs(self):
        pages = sorted(p.parent.name for p in (self.out / 'wells').glob('*/index.html'))
        self.assertEqual(len(pages), 121)
        self.assertIn('33-053-09999', pages)                       # file 90001, added from the permit layer
        future = (self.out / 'wells' / '33-053-09999' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('<h1>Well file 90001</h1>', future)             # the permit layer has no names
        self.assertIn('Permitted location, not yet drilled (LOC)', future)
        self.assertIn('From the permit layer', future)
        self.assertEqual(sum(1 for p in pages if p.startswith('file-')), 1)   # the well without an API
        duplicates = [p for p in pages if re.fullmatch(r'33-\d{3}-\d{5}-\d+', p)]
        self.assertEqual(len(duplicates), 1)                       # duplicate API got a file-number suffix
        for name in ('index.html', 'wells/index.html', 'wells/index.json', 'operators/index.html', 'counties/index.html',
                     '404.html', 'robots.txt', 'sitemap.xml', 'sitemap-pages.xml', 'sitemap-wells-1.xml', 'assets/well.css',
                     'assets/search.js', 'assets/page.js', '.nojekyll', 'build.json'):
            self.assertTrue((self.out / name).exists(), name)
        self.assertFalse((self.out / 'CNAME').exists())            # github.io host with a path: no CNAME
        self.assertFalse((self.out / 'units').exists())            # no --data: no unit or order pages, no nav entry
        self.assertNotIn('>Spacing units</a>', (self.out / 'index.html').read_text(encoding='utf-8'))
        self.assertIn('No spacing-unit data', self.summary.read_text(encoding='utf-8'))

    def test_well_page_content_is_escaped_and_complete(self):
        hostile = next(p for p in (self.out / 'wells').glob('*/index.html') if 'alert(1)' in p.read_text(encoding='utf-8'))
        html = hostile.read_text(encoding='utf-8')
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt; 1H', html)
        self.assertIn('EVIL &amp; CO &quot;QUOTES&quot; LLC', html)
        parser = WellCounter()
        parser.feed(html)
        self.assertEqual(parser.h1, 1)
        self.assertTrue(any(h and h.startswith('https://www.google.com/maps/dir/?api=1&destination=') for h in parser.links))
        self.assertTrue(any(h and h.startswith('https://bakkensubsurface.com/viewer/?dataset=full&well=') for h in parser.links))
        self.assertIn('<link rel="canonical" href="https://example.github.io/repo/wells/', html)
        self.assertIn('"@type":"Place"', html)
        self.assertIn('"geo":{"@type":"GeoCoordinates"', html)
        self.assertIn('/repo/assets/well.css', html)
        self.assertIn('Nearby wells', html)
        self.assertIn('Other published attributes (', html)
        self.assertNotIn('<th>well_name</th>', html)                 # mapped columns are shown as facts, not repeated
        self.assertRegex(html, r'Sec \d+ T\d+N R\d+W')
        self.assertRegex(html, r'[\d,]+ ft F[NS]L · [\d,]+ ft F[EW]L')
        self.assertRegex(html, r'API \d\d-\d{3}-\d{5}-\d\d-\d\d')      # DMR's own API formatting from api_no

    def test_well_without_coordinates_or_name(self):
        html_by_slug = {p.parent.name: p.read_text(encoding='utf-8') for p in (self.out / 'wells').glob('*/index.html')}
        unnamed = [h for h in html_by_slug.values() if '<h1>Well file ' in h]
        self.assertEqual(len(unnamed), 3)                          # one blank name in OGD_Wells + two permit-layer records
        self.assertEqual(sum('Status not published' in h for h in unnamed), 1)
        no_coords = [h for h in html_by_slug.values() if 'map links are unavailable' in h]
        self.assertEqual(len(no_coords), 1)
        self.assertNotIn('"geo"', no_coords[0])

    def test_search_index_matches_search_js_layout(self):
        index = json.loads((self.out / 'wells' / 'index.json').read_text(encoding='utf-8'))
        self.assertEqual(index['fields'], ['name', 'api', 'file', 'op', 'county', 'status', 'type', 'lat', 'lon', 'slug'])
        self.assertEqual(len(index['wells']), 121)
        row = next(r for r in index['wells'] if r[0] == 'HAWKINSON 1-21H')
        self.assertEqual(index['statuses'][row[5]], 'A')
        self.assertIsNone(row[9])                                  # slug derivable from the API
        self.assertEqual(index['statusGloss']['PA'], 'Plugged and abandoned')
        js = (self.out / 'assets' / 'search.js').read_text(encoding='utf-8')
        self.assertIn("export const FIELDS = ['name', 'api', 'file', 'op', 'county', 'status', 'type', 'lat', 'lon', 'slug'];", js)

    def test_sitemaps_and_robots(self):
        ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        index = ET.parse(self.out / 'sitemap.xml').getroot()
        maps = [el.text for el in index.findall('s:sitemap/s:loc', ns)]
        self.assertEqual(maps, ['https://example.github.io/repo/sitemap-pages.xml', 'https://example.github.io/repo/sitemap-townships.xml',
                                'https://example.github.io/repo/sitemap-wells-1.xml'])
        wells = ET.parse(self.out / 'sitemap-wells-1.xml').getroot().findall('s:url/s:loc', ns)
        self.assertEqual(len(wells), 121)
        self.assertTrue(all(el.text.startswith('https://example.github.io/repo/wells/') for el in wells))
        self.assertIn('Sitemap: https://example.github.io/repo/sitemap.xml', (self.out / 'robots.txt').read_text())

    def test_operator_and_county_pages_link_every_well(self):
        linked = set()
        for page in list((self.out / 'operators').glob('*/index.html')):
            parser = WellCounter()
            parser.feed(page.read_text(encoding='utf-8'))
            self.assertEqual(parser.h1, 1)
            linked.update(h.split('/wells/')[1].rstrip('/') for h in parser.links if h and '/wells/' in h)
        self.assertEqual(len(linked), 121)
        county = next((self.out / 'counties').glob('*/index.html')).read_text(encoding='utf-8')
        self.assertIn('Permitted locations not yet drilled', county) if 'LOC' in county else None
        self.assertIn('<h1>', county)

    def test_sitemap_lastmod_is_the_dmr_file_date_when_known(self):
        ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        # the fixture's download record has no Last-Modified: the retrieval date is the fallback
        self.assertEqual(self.report['layerDate'], None)
        self.assertEqual(ET.parse(self.out / 'sitemap-wells-1.xml').getroot().find('s:url/s:lastmod', ns).text, '2026-10-17')
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'dmr'
            fixture.write_fixture(source, wells=20, permits=False)
            record = source / 'download-provenance.json'
            provenance = json.loads(record.read_text(encoding='utf-8'))
            provenance['archives'][0]['lastModified'] = 'Fri, 06 Nov 2026 09:17:41 GMT'
            record.write_text(json.dumps(provenance), encoding='utf-8')
            report = bwp.build(source, Path(tmp) / 'site', 'https://example.github.io/repo/', generator='abc123' * 7)
            self.assertEqual(report['layerDate'], '2026-11-06')
            self.assertEqual(report['sourceDate'], '2026-10-17')          # retrieval date still recorded
            self.assertEqual(report['generator'], 'abc123' * 7)
            lastmods = {el.text for el in ET.parse(Path(tmp) / 'site' / 'sitemap-wells-1.xml').getroot().findall('s:url/s:lastmod', ns)}
            self.assertEqual(lastmods, {'2026-11-06'})
        self.assertEqual(bwp.http_date('not a date'), None)
        self.assertEqual(bwp.http_date(None), None)

    def test_custom_domain_writes_cname(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'site'
            report = bwp.build(self.source, out, 'https://wells.bakkensubsurface.com', limit=20)
            self.assertEqual((out / 'CNAME').read_text().strip(), 'wells.bakkensubsurface.com')
            self.assertEqual(report['basePath'], '')
            page = next((out / 'wells').glob('*/index.html')).read_text(encoding='utf-8')
            self.assertIn('href="/assets/well.css"', page)

    def test_site_verification_tag_is_on_every_page_only_when_given(self):
        page = (self.out / 'index.html').read_text(encoding='utf-8')
        self.assertNotIn('google-site-verification', page)
        self.assertIsNone(self.report['siteVerification'])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'site'
            report = bwp.build(self.source, out, 'https://example.github.io/repo/', limit=20, site_verification=' abc"<DEF>123 ')
            self.assertEqual(report['siteVerification'], 'abc"<DEF>123')
            tag = '<meta name="google-site-verification" content="abc&quot;&lt;DEF&gt;123">'
            pages = [p for p in out.rglob('*.html')]
            self.assertGreater(len(pages), 20)
            for page_path in pages:
                self.assertIn(tag, page_path.read_text(encoding='utf-8'), page_path)


if __name__ == '__main__':
    unittest.main()
