"""Offline tests for the production panel (scripts/wells/production.py) on the fixture feed."""
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells import build_well_pages as bwp  # noqa: E402
from wells import fixture, production  # noqa: E402


def entry(first, last, oil):
    return {'api': '33053000010000', 'pools': ['BAKKEN'], 'first': first, 'last': last, 'reported': sum(1 for x in oil if x is not None),
            'v': {'oil': oil, 'water': [None if x is None else x + 1 for x in oil], 'gas': [None if x is None else x * 2 for x in oil],
                  'gasSold': [None if x is None else x for x in oil], 'flared': [0 if x is not None else None for x in oil],
                  'days': [None if x is None else 30 for x in oil]}}


class HelperTests(unittest.TestCase):
    def test_months_and_summary(self):
        e = entry('2025-11', '2026-03', [100, None, 50, 0, 25])
        self.assertEqual(production.span_months(e), ['2025-11', '2025-12', '2026-01', '2026-02', '2026-03'])
        s = production.summarize(e)
        self.assertEqual((s['total_oil'], s['total_gas'], s['total_water']), (175, 350, 179))
        blank = production.summarize(entry('2024-01', '2024-02', [None, None]))
        self.assertEqual((blank['total_oil'], blank['last12_oil'], blank['last12_known'], blank['peak']), (None, None, 0, None))
        self.assertEqual((s['unknown'], s['span'], s['lastProducing']), (1, 5, '2026-03'))
        self.assertEqual(s['peak'], {'month': '2025-11', 'oil': 100})
        self.assertEqual((s['last12_oil'], s['last12_known']), (175, 4))
        self.assertEqual(production.month_label('2026-03'), 'Mar 2026')
        self.assertEqual(production.month_label('2026-03', True), 'Mar 26')
        self.assertEqual(production.next_month('2025-12'), '2026-01')
        self.assertEqual(production.number(None), '—')
        self.assertEqual(production.number(12345), '12,345')

    def test_chart_breaks_at_unknown_months_and_labels_years(self):
        e = entry('2025-11', '2026-03', [100, None, 50, 0, 25])
        svg = production.chart_svg(e)
        self.assertTrue(svg.startswith('<svg class="prodchart show-oil"'))
        self.assertIn('<g class="s-oil">', svg)
        self.assertNotIn('<g class="s-gas">', svg)                     # gas and water are drawn in the browser
        self.assertNotIn('<g class="s-water">', svg)
        oil = svg[svg.index('<g class="s-oil">'):]
        path = oil[oil.index('<path d="') + 9:]
        path = path[:path.index('"')]
        self.assertEqual(path.count('M'), 2)          # one gap, two sub-paths
        self.assertEqual(path.count('L'), 2)
        self.assertIn('>Oil, bbl per month<', oil)
        self.assertIn('Nov 25', svg)
        self.assertIn('Mar 26', svg)
        gas = production.series_group(e['v']['gas'], 'gas', 5)
        self.assertTrue(gas.startswith('<g class="s-gas"><g class="grid"><line x1="52" y1="198" x2="630" y2="198"/><text x="46" y="202">0</text>'))
        self.assertIn('>Gas, mcf per month<', gas)
        self.assertIn('<text x="46" y="14">200</text>', gas)          # the top tick is the series maximum (the fixture's gas is twice its oil)
        self.assertIn('<text x="46" y="108">100</text>', gas)
        self.assertIn('<g class="s-gas">', production.chart_svg(e, series=('oil', 'gas')))
        long = entry('2015-05', '2026-08', [10] * 136)
        svg = production.chart_svg(long)
        self.assertIn('>2016<', svg)
        self.assertIn('>2026<', svg)
        flat = entry('2024-01', '2024-02', [0, 0])
        self.assertIn('M', production.chart_svg(flat))   # a zero-only well still draws a baseline
        single = entry('2024-01', '2024-01', [7])
        self.assertIn('show-oil', production.chart_svg(single))

    def test_browser_chart_matches_python(self):
        """templates/production.js must draw a series exactly as series_group does (same geometry and rounding)."""
        import shutil
        import subprocess
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        import random
        rng = random.Random(11)
        cases = [{'values': [100, None, 50, 0, 25], 'series': 'gas', 'n': 5},
                 {'values': [None, None], 'series': 'water', 'n': 2},
                 {'values': [7], 'series': 'oil', 'n': 1},
                 {'values': [0, 0, 0], 'series': 'gas', 'n': 3},
                 {'values': [2033, 1696, 1658, None, 1200, 5, 2033], 'series': 'water', 'n': 7}]
        for _ in range(6):
            n = rng.randint(2, 284)
            cases.append({'values': [None if rng.random() < 0.15 else rng.randint(0, 50000) for _ in range(n)], 'series': rng.choice(['oil', 'gas', 'water']), 'n': n})
        module = (Path(__file__).resolve().parents[1] / 'wells' / 'templates' / 'production.js').as_uri()
        with tempfile.TemporaryDirectory() as tmp:
            cases_path = Path(tmp) / 'cases.json'
            cases_path.write_text(json.dumps(cases), encoding='utf-8')
            script = ('import {seriesGroup} from %r; import {readFileSync} from "node:fs"; '
                      'const cases = JSON.parse(readFileSync(process.argv[1], "utf8")); '
                      'process.stdout.write(cases.map(c => seriesGroup(c.values, c.series, c.n)).join("\\n"));' % module)
            result = subprocess.run([node, '--input-type=module', '-e', script, str(cases_path)], capture_output=True, text=True, check=True)
        expected = [production.series_group(c['values'], c['series'], c['n']) for c in cases]
        self.assertEqual(result.stdout.split('\n'), expected)

    def test_pdf_span_and_unit_note(self):
        feed = {'pdf': {'firstMonth': '2003-01', 'lastMonth': '2015-04'}}
        self.assertEqual(production.pdf_span(feed), ('2003-01', '2015-04'))
        self.assertIsNone(production.pdf_span({'pdf': None}))
        self.assertIsNone(production.pdf_span(None))
        entry = {'first': '2015-05', 'unitPool': True}
        note = production.unit_note(feed, {'extras': {'spud': '1981-06-02'}}, entry)
        self.assertIn('Before May 2015 this well', note)
        self.assertIn('its own figures start in May 2015', note)
        self.assertEqual(production.unit_note(feed, {'extras': {}}, entry), note)                        # spud unknown: say it
        self.assertEqual(production.unit_note(feed, {'extras': {'spud': '2019-03-01'}}, entry), '')      # drilled after the PDF years
        self.assertEqual(production.unit_note(feed, {'extras': {}}, {'first': '2015-05'}), '')           # not flagged
        self.assertEqual(production.unit_note({}, {'extras': {}}, entry), '')                            # no PDF months in the feed

    def test_table_csv_payload_and_loader(self):
        e = entry('2024-01', '2025-03', [None] + [10] * 14)
        table = production.table_html(e)
        self.assertEqual(table.count('<tr><th><time'), 12)                    # last twelve months only
        self.assertIn('<time datetime="2025-03">Mar 2025</time>', table)
        self.assertNotIn('2024-01', table)
        self.assertNotIn('class="num"', table)                                 # figures align by position (CSS), not by a class per cell
        self.assertIn('<th>Oil bbl</th>', table)
        payload = json.loads(production.csv_payload(e))
        self.assertEqual(payload['start'], '2024-01')
        self.assertEqual(payload['oil'], ',' + ','.join(['10'] * 14))          # an unknown month is an empty field
        self.assertEqual(payload['days'], ',' + ','.join(['30'] * 14))
        self.assertEqual(sorted(payload), ['days', 'flared', 'gas', 'gasSold', 'oil', 'start', 'water'])
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / 'feed.json'
            good.write_text(json.dumps({'version': 1, 'kind': 'well-production', 'months': ['2024-01'], 'wells': {}}), encoding='utf-8')
            self.assertEqual(production.load_production(good)['months'], ['2024-01'])
            bad = Path(tmp) / 'bad.json'
            bad.write_text(json.dumps({'version': 2, 'kind': 'well-production', 'months': [], 'wells': {}}), encoding='utf-8')
            with self.assertRaises(ValueError):
                production.load_production(bad)

class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.source, cls.out = root / 'source', root / 'site'
        fixture.write_fixture(cls.source, wells=40, permits=False)
        cls.rows = fixture.make_rows(40)
        cls.report = bwp.build(cls.source, cls.out, 'https://example.github.io/repo/', data=cls.source / 'well-index-data.json.gz',
                               production=cls.source / 'well-production.json.gz')
        cls.build_json = json.loads((cls.out / 'build.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def page(self, case):
        api = self.rows[fixture.PRODUCTION_CASES[case]]['api']
        slug = f'{api[:2]}-{api[2:5]}-{api[5:10]}'
        return (self.out / 'wells' / slug / 'index.html').read_text(encoding='utf-8')

    def test_report_and_build_json(self):
        self.assertEqual(self.report['wellsWithProduction'], 5)
        p = self.build_json['production']
        self.assertEqual((p['firstMonth'], p['lastMonth'], p['months'], p['feedWells']), ('2024-01', '2024-12', 12, 6))
        self.assertEqual((p['pdfFirstMonth'], p['pdfLastMonth'], p['unitPoolWells']), ('2024-01', '2024-06', 1))
        self.assertEqual((p['matchedByApi'], p['feedWellsNotInLayer'], p['generated']), (1, 1, '2026-11-07T16:50:00Z'))
        self.assertEqual(p['file'], 'well-production.json.gz')
        self.assertTrue((self.out / 'assets' / 'production.js').exists())
        summary = bwp.markdown_summary(self.report)
        self.assertIn('**Production** for 5 wells', summary)
        self.assertIn('months 2024-01 to 2024-06 read from the PDF reports, 1 wells reported only inside a unit total', summary)

    def test_full_year_well_page(self):
        html = self.page('full')
        self.assertIn('<section class="panel production"><h2>Production</h2>', html)
        self.assertIn('<svg class="prodchart show-oil"', html)
        self.assertIn('<b>7,575</b><span>bbl oil, Jan 24 to Dec 24</span>', html)        # unknown April not counted
        self.assertIn('<dt>Unknown months</dt><dd>1 of 12', html)
        self.assertIn('<dt>Last month with oil</dt><dd>Dec 2024</dd>', html)
        self.assertIn('<dt>Peak month</dt><dd>Jan 2024, 1,200 bbl</dd>', html)
        self.assertIn('<td>—</td>', html)                                                   # April in the table
        self.assertIn('<time datetime="2024-09">Sep 2024</time></th><td>0</td>', html)   # a reported zero is a zero
        self.assertIn('id="production-data"', html)
        self.assertIn('"oil":"1200,980,870,,760', html)
        self.assertNotIn('<g class="s-gas">', html)                                       # drawn in the browser on request
        self.assertIn('<script type="module" src="/repo/assets/production.js">', html)
        self.assertIn('As first reported to NDIC', html)
        self.assertIn('Months from Jan 2024 to Jun 2024 are read from the Division&#x27;s PDF reports', html.replace("'", '&#x27;'))
        self.assertNotIn('reported only as a unit total', html)
        self.assertIn('data-name="33-', html)

    def test_confidential_short_and_api_matched_wells(self):
        confidential = self.page('confidential')
        self.assertIn('<b>—</b><span>bbl oil, Mar 24 to May 24</span>', confidential)      # all unknown: no total, not zero
        self.assertIn('<b>—</b><span>bbl oil, last 12 months (0 known)</span>', confidential)
        self.assertIn('<dt>Unknown months</dt><dd>3 of 3', confidential)
        self.assertNotIn('<dt>Last month with oil</dt>', confidential)
        short = self.page('short')
        self.assertIn('<dt>Pools reported</dt><dd>MADISON, TYLER</dd>', short)
        self.assertIn('<b>840</b><span>bbl oil, Oct 24 to Dec 24</span>', short)
        by_api = self.page('byApi')
        self.assertIn('<b>90</b><span>bbl oil, Jun 24 to Jul 24</span>', by_api)

    def test_well_without_production_says_so_without_zero(self):
        html = self.page('none')
        self.assertIn('does not appear on its own in the <a href="https://www.dmr.nd.gov/oilgas/mprindex.asp"', html)
        self.assertIn('Before Jul 2024 a well in a unitized pool was listed only inside its unit', html)
        self.assertIn('Jan 2024 to Dec 2024', html)
        self.assertNotIn('prodchart', html)
        self.assertNotIn('assets/production.js', html)

    def test_unit_pool_well_note(self):
        html = self.page('unitPool')
        self.assertIn('<b>450</b><span>bbl oil, Jul 24 to Dec 24</span>', html)
        self.assertIn('Before Jul 2024 this well', html)
        self.assertIn('reported only as a unit total, so the reports hold no figures for the well itself in those years; its own figures start in Jul 2024.', html)

    def test_build_without_feed_has_no_panels(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'site'
            report = bwp.build(self.source, out, 'https://example.github.io/repo/', limit=5)
            self.assertEqual(report['wellsWithProduction'], 0)
            self.assertIsNone(report['production'])
            page = next((out / 'wells').glob('*/index.html')).read_text(encoding='utf-8')
            self.assertNotIn('class="panel production"', page)
            self.assertIn('No production feed', bwp.markdown_summary(report))


if __name__ == '__main__':
    unittest.main()
