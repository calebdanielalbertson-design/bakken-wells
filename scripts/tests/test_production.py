"""Offline tests for the production panel (scripts/wells/production.py) on the fixture feed."""
import gzip
import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells import build_well_pages as bwp  # noqa: E402
from wells import fixture, production  # noqa: E402

MODULE = Path(__file__).resolve().parents[1] / 'wells' / 'templates' / 'production.js'


def entry(first, last, oil, days=30):
    return {'api': '33053000010000', 'pools': ['BAKKEN'], 'first': first, 'last': last, 'reported': sum(1 for x in oil if x is not None),
            'v': {'oil': oil, 'water': [None if x is None else x + 1 for x in oil], 'gas': [None if x is None else x * 2 for x in oil],
                  'gasSold': [None if x is None else x for x in oil], 'flared': [0 if x is not None else None for x in oil],
                  'days': [None if x is None else days for x in oil]}}


def run_node(script, *args):
    node = shutil.which('node')
    if not node:
        raise unittest.SkipTest('node is not installed')
    return subprocess.run([node, '--input-type=module', '-e', script, *args], capture_output=True, text=True, check=True, encoding='utf-8').stdout


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
        self.assertEqual(production.month_label('2026-03', True), 'Mar ’26')
        self.assertEqual(production.next_month('2025-12'), '2026-01')
        self.assertEqual(production.add_months('2025-11', 14), '2027-01')
        self.assertEqual(production.days_in('2024-02'), 29)
        self.assertEqual(production.number(None), '—')
        self.assertEqual(production.number(12345), '12,345')

    def test_numbers_and_ticks(self):
        self.assertEqual([production.compact(v) for v in (None, 0, 9999, 12250, 12345, 123456, 1500000, 1840000, 12345678)],
                         ['—', '0', '9,999', '12.2K', '12.3K', '123K', '1.5M', '1.84M', '12.3M'])   # 12,250 is a tie: to even
        self.assertEqual(production.nice_ticks(1005), [0, 500, 1000, 1500])
        self.assertEqual(production.nice_ticks(7), [0, 2.5, 5, 7.5])
        self.assertEqual(production.nice_ticks(0), [0, 1])
        self.assertEqual(production.nice_ticks(None), [0, 1])
        self.assertEqual(production.nice_ticks(0.03), [0, 0.01, 0.02, 0.03])
        self.assertEqual(production.tick_label(2.5, [0, 2.5, 5, 7.5]), '2.5')
        self.assertEqual(production.tick_label(0.02, [0, 0.01, 0.02, 0.03]), '0.02')
        self.assertEqual(production.tick_label(15000, [0, 5000, 10000, 15000]), '15K')
        self.assertEqual(production.tick_label(5000, [0, 2500, 5000, 7500]), '5,000')
        self.assertEqual(production.fmt(2.5), '2')
        self.assertEqual(production.fmt(3.5), '4')
        self.assertEqual(production.rate(300, 30), 10)
        self.assertIsNone(production.rate(300, 0))        # no producing days: no rate, not infinity
        self.assertIsNone(production.rate(None, 30))

    def test_metrics(self):
        oil = [1200, 980, 870, None, 760, 700, 650, 640, 0, 610, 590, 575] + [500 - 10 * i for i in range(12)]
        e = entry('2024-01', '2025-12', oil)
        m = production.metrics(e)
        self.assertEqual((m['n'], m['knownMonths'], m['unknown'], m['cumOil']), (24, 23, 1, sum(x for x in oil if x is not None)))
        self.assertEqual(m['peakRate']['month'], '2024-01')
        self.assertEqual((m['peakRate']['rate'], m['peakRate']['days']), (40, 30))
        self.assertEqual((m['peakMonth']['month'], m['monthsToPeak']), ('2024-01', 1))
        last12 = sum(oil[12:])
        self.assertEqual((m['last12Oil'], m['last12Known']), (last12, 12))
        self.assertIsNone(m['yoy'])                                              # the twelve before have an unknown month: no comparison
        full = [800 if x is None else x for x in oil]
        self.assertAlmostEqual(production.metrics(entry('2024-01', '2025-12', full))['yoy'], (sum(full[12:]) - sum(full[:12])) / sum(full[:12]) * 100)
        self.assertIsNone(production.metrics(entry('2024-01', '2024-12', oil[:12]))['yoy'])
        self.assertAlmostEqual(m['gor12'], 2000)                                 # the fixture's gas is twice its oil
        self.assertAlmostEqual(m['gorYear1'], 2000)
        self.assertAlmostEqual(m['wcut12'], 100 * (last12 + 12) / (2 * last12 + 12))
        self.assertAlmostEqual(m['sold12'], 50)
        self.assertEqual(m['flared12'], 0)
        self.assertEqual(m['firstMonths'][3], {'oil': 3050, 'known': 3})
        self.assertEqual(m['firstMonths'][6], {'oil': 4510, 'known': 5})
        self.assertEqual(m['firstMonths'][24]['known'], 23)
        self.assertAlmostEqual(m['year1Share'], 100 * 7575 / m['cumOil'])
        self.assertAlmostEqual(m['latestRate'], 390 / 30)
        self.assertAlmostEqual(m['shareOfPeak'], 100 * (390 / 30) / 40)
        self.assertEqual(m['uptime12'], 100 * 360 / 365)
        self.assertEqual((m['lastProducing'], m['pools']), ('2025-12', ['BAKKEN']))
        blank = production.metrics(entry('2024-03', '2024-05', [None, None, None]))
        self.assertEqual((blank['cumOil'], blank['peakRate'], blank['peakMonth'], blank['yoy'], blank['gor12'], blank['latestRate'], blank['uptime12']),
                         (None, None, None, None, None, None, None))
        self.assertEqual(blank['firstMonths'][3], {'oil': None, 'known': 0})
        self.assertIsNone(blank['year1Share'])
        d = production.derive(entry('2024-01', '2024-03', [100, 0, None]))
        self.assertEqual(d['oilRate'], [100 / 30, 0, None])
        self.assertEqual(d['gor'], [2000, None, None])                            # no oil: no ratio
        self.assertEqual(d['wcut'], [100 * 101 / 201, 100, None])

    def test_strip_chart(self):
        e = entry('2025-11', '2026-03', [100, None, 50, 0, 25])
        svg = production.chart_svg(e)
        self.assertTrue(svg.startswith('<svg class="prodchart main" viewBox="0 0 820 160" role="img" aria-label="Oil, barrels per producing day, Nov 2025 to Mar 2026">'))
        self.assertIn('<g class="grid"><line x1="58" x2="746" y1="134" y2="134"/><text x="50" y="138" text-anchor="end">0</text>', svg)
        self.assertEqual(svg.count('<rect class="band"'), 1)                     # the unknown December
        self.assertIn('<text class="t" x="58" y="14">Oil<tspan> bbl/d</tspan></text>', svg)
        path = svg[svg.index('<path class="s oil" d="') + 23:]
        path = path[:path.index('"')]
        self.assertEqual((path.count('M'), path.count('L')), (2, 2))            # one gap, two sub-paths
        self.assertIn('<circle class="end oil"', svg)
        self.assertIn('<text class="end"', svg)
        self.assertIn('>Nov ’25<', svg)
        self.assertIn('>Mar ’26<', svg)
        self.assertNotIn('class="s gas"', svg)                                   # gas and water are drawn in the browser
        long = production.chart_svg(entry('2015-05', '2026-08', [10] * 136))
        self.assertIn('>2016<', long)
        self.assertIn('>2026<', long)
        self.assertNotIn('>2015<', long)
        flat = production.chart_svg(entry('2024-01', '2024-02', [0, 0]))
        self.assertIn('<path class="s oil" d="M58,134 L746,134"/>', flat)       # a zero-only well still draws a baseline
        blank = production.chart_svg(entry('2024-01', '2024-02', [None, None]))
        self.assertIn('<path class="s oil" d="M58,134"/>', blank)
        single = production.chart_svg(entry('2024-01', '2024-01', [7]))
        self.assertIn('<circle class="end oil" cx="402"', single)               # a single month sits mid-plot
        pct = production.strip_svg(production.GEO['diag'], production.TOP, 'Water cut', '%', 'pct', [33.3, 50, 66.7], 0, 3, True, '2024-01', 3)
        self.assertIn('>100<', pct)
        self.assertIn('<text class="end" x="245" y="', pct)
        self.assertIn('>67%<', pct)

    def test_browser_chart_matches_python(self):
        """templates/production.js must draw a strip exactly as strip_svg does (same geometry, rounding and text)."""
        rng = random.Random(3)
        cases = [{'values': [100, None, 50, 0, 25], 'cls': 'gas', 'idx0': 0, 'idx1': 5, 'n': 5, 'first': '2025-11', 'geo': 'main'},
                 {'values': [None, None], 'cls': 'water', 'idx0': 0, 'idx1': 2, 'n': 2, 'first': '2024-01', 'geo': 'main'},
                 {'values': [7], 'cls': 'oil', 'idx0': 0, 'idx1': 1, 'n': 1, 'first': '2024-01', 'geo': 'main'},
                 {'values': [0, 0, 0], 'cls': 'gas', 'idx0': 0, 'idx1': 3, 'n': 3, 'first': '2024-01', 'geo': 'diag'},
                 {'values': [0.3, 0.25, 0.1, None, 0.05], 'cls': 'oil', 'idx0': 0, 'idx1': 5, 'n': 5, 'first': '2024-01', 'geo': 'main'},
                 {'values': [33.3, 50, 66.7, 100, 0], 'cls': 'pct', 'idx0': 0, 'idx1': 5, 'n': 5, 'first': '2024-01', 'geo': 'diag'}]
        for _ in range(30):
            n = rng.randint(2, 284)
            values = [None if rng.random() < 0.15 else rng.random() * rng.choice([1, 10, 1000, 60000, 3e6]) for _ in range(n)]
            if rng.random() < 0.3:
                values = [None if v is None else round(v) for v in values]
            idx0, idx1 = rng.choice([0, 0, max(0, n - 60)]), rng.choice([n, n, min(36, n)])
            if idx1 <= idx0:
                idx0, idx1 = 0, n
            cases.append({'values': values, 'cls': rng.choice(['oil', 'gas', 'water']), 'idx0': idx0, 'idx1': idx1, 'n': n,
                          'first': f'{rng.randint(2003, 2024)}-{rng.randint(1, 12):02d}', 'geo': rng.choice(['main', 'diag'])})
        expected = [production.strip_svg(production.GEO[c['geo']], production.TOP, 'Oil', 'bbl/d', c['cls'], c['values'], c['idx0'], c['idx1'], True, c['first'], c['n'])
                    for c in cases]
        with tempfile.TemporaryDirectory() as tmp:
            cases_path = Path(tmp) / 'cases.json'
            cases_path.write_text(json.dumps(cases), encoding='utf-8')
            script = ('import {stripSvg, GEO, TOP} from %r; import {readFileSync} from "node:fs"; '
                      'const cases = JSON.parse(readFileSync(process.argv[1], "utf8")); '
                      'process.stdout.write(cases.map(c => stripSvg(GEO[c.geo], TOP, "Oil", "bbl/d", c.cls, c.values, c.idx0, c.idx1, true, c.first, c.n).svg)'
                      '.join(String.fromCharCode(10)));' % MODULE.as_uri())
            out = run_node(script, str(cases_path))
        self.assertEqual(out.split('\n'), expected)

    def test_browser_numbers_match_python(self):
        values = [0, 7, 999, 9999, 12250, 12345, 99950, 123456, 1500000, 1840000, 9995000, 12345678, 2.5, 0.125]
        script = ('import {compact, fixed, fmt, niceTicks, tickLabel, monthLabel, addMonths} from %r; '
                  'const v = %s; process.stdout.write(JSON.stringify({compact: v.map(compact), fmt: [2.5, 3.5, 4.4999, 1e6 + 0.5].map(fmt), '
                  'fixed: [[12.25, 1], [0.125, 2], [1.005, 2]].map(([a, b]) => fixed(a, b)), ticks: [1005, 7, 0, 0.03, 60000].map(niceTicks), '
                  'labels: [[2.5, [0, 2.5, 5, 7.5]], [15000, [0, 5000, 10000, 15000]], [0.02, [0, 0.01, 0.02, 0.03]]].map(([a, b]) => tickLabel(a, b)), '
                  'months: [monthLabel("2026-03"), monthLabel("2026-03", true), addMonths("2025-11", 14)]}));' % (MODULE.as_uri(), json.dumps(values)))
        got = json.loads(run_node(script))
        self.assertEqual(got['compact'], [production.compact(v) for v in values])
        self.assertEqual(got['fmt'], [production.fmt(v) for v in (2.5, 3.5, 4.4999, 1e6 + 0.5)])
        self.assertEqual(got['fixed'], [production.fixed(a, b) for a, b in ((12.25, 1), (0.125, 2), (1.005, 2))])
        self.assertEqual(got['ticks'], [production.nice_ticks(v) for v in (1005, 7, 0, 0.03, 60000)])
        self.assertEqual(got['labels'], [production.tick_label(a, b) for a, b in ((2.5, [0, 2.5, 5, 7.5]), (15000, [0, 5000, 10000, 15000]), (0.02, [0, 0.01, 0.02, 0.03]))])
        self.assertEqual(got['months'], ['Mar 2026', 'Mar ’26', '2027-01'])

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
        self.assertIn('<th>Month</th><th>Oil bbl</th><th>Gas mcf</th><th>Water bbl</th><th>Days</th><th>Oil bbl/d</th><th>GOR scf/bbl</th><th>Water cut</th>', table)
        self.assertIn('<time datetime="2025-03">Mar 2025</time></th><td>10</td><td>20</td><td>11</td><td>30</td><td>0</td><td>2,000</td><td>52%</td></tr>', table)
        self.assertNotIn('2024-01', table)
        self.assertNotIn('class="num"', table)                                 # figures align by position (CSS), not by a class per cell
        zero_days = production.table_html(entry('2024-01', '2024-01', [0], days=0))
        self.assertIn('<td>0</td><td>0</td><td>1</td><td>0</td><td>—</td><td>—</td><td>100%</td>', zero_days)   # no days: no rate
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
        css = (self.out / 'assets' / 'well.css').read_text(encoding='utf-8')
        self.assertIn('--oil:#176f17;--gas:#b67647;--water:#0064aa', css)       # the brand series colours
        summary = bwp.markdown_summary(self.report)
        self.assertIn('**Production** for 5 wells', summary)
        self.assertIn('months 2024-01 to 2024-06 read from the PDF reports, 1 wells reported only inside a unit total', summary)

    def test_full_year_well_page(self):
        html = self.page('full')
        self.assertIn('<meta name="theme-color" content="#ffffff">', html)
        self.assertIn('<section class="panel production"><h2>Production</h2><p class="sub">12 months reported, Jan 2024 to Dec 2024, BAKKEN pool.</p>', html)
        self.assertIn('<span class="l">Cumulative oil</span><b>7,575<small>bbl</small></b><span class="d">Jan ’24 to Dec ’24 · <strong>11</strong> of 12 months known</span>', html)
        self.assertIn('<span class="l">Peak oil rate</span><b>40<small>bbl/d</small></b><span class="d">Jan 2024 · 1,200 bbl in 30 days</span>', html)
        self.assertIn('<span class="l">Oil, last 12 months</span><b>7,575<small>bbl</small></b><span class="d">11 of 12 months known</span>', html)
        self.assertIn('<span class="l">Gas–oil ratio, last 12 months</span><b>3,000<small>scf/bbl</small></b><span class="d">first year <strong>3,000</strong> scf/bbl</span>', html)
        self.assertIn('<span class="l">Water cut, last 12 months</span><b>67<small>%</small></b>', html)
        self.assertIn('<span class="l">Gas sold, last 12 months</span><b>67<small>%</small></b><span class="d">of gas produced · <strong>7,575</strong> mcf reported flared</span>', html)
        self.assertIn('<div class="chartwrap"><svg class="prodchart main" viewBox="0 0 820 160"', html)
        self.assertIn('<div class="diag"></div>', html)
        self.assertIn('<dt>Months to peak</dt><dd>1 (Jan 2024)</dd>', html)
        self.assertIn('<dt>Peak month rate</dt><dd>40 bbl/d on 30 days</dd>', html)
        self.assertIn('<dt>First 3 months, oil</dt><dd>3,050 bbl</dd>', html)
        self.assertIn('<dt>First 6 months, oil</dt><dd>4,510 bbl (5 of 6 known)</dd>', html)
        self.assertIn('<dt>Share of cumulative in year 1</dt><dd>100%</dd>', html)
        self.assertIn('<dt>Last month with oil</dt><dd>Dec 2024</dd>', html)
        self.assertIn('<dt>Latest oil rate</dt><dd>19 bbl/d</dd>', html)
        self.assertIn('<dt>Latest rate as share of peak</dt><dd>47.9%</dd>', html)
        self.assertIn('<dt>Uptime, last 12 months</dt><dd>89% of days</dd>', html)          # 300 producing days over the 336 calendar days of the months with known days
        self.assertIn('<dt>Unknown months</dt><dd>1 of 12', html)
        self.assertIn('<td>—</td>', html)                                                   # April in the table
        self.assertIn('<time datetime="2024-09">Sep 2024</time></th><td>0</td><td>0</td><td>0</td><td>0</td><td>—</td>', html)   # a reported zero is a zero; no days, no rate
        self.assertIn('id="production-data"', html)
        self.assertIn('"oil":"1200,980,870,,760', html)
        self.assertNotIn('class="s gas"', html)                                                  # drawn in the browser
        self.assertNotIn('class="controls"', html)                                               # the controls need the script, so the script adds them
        self.assertIn('<script type="module" src="/repo/assets/production.js">', html)
        self.assertIn('As first reported to NDIC', html)
        self.assertIn('a month reported without producing days has no rate', html)
        self.assertIn('Months from Jan 2024 to Jun 2024 are read from the Division&#x27;s PDF reports', html.replace("'", '&#x27;'))
        self.assertNotIn('reported only as a unit total', html)
        self.assertIn('data-name="33-', html)

    def test_confidential_short_and_api_matched_wells(self):
        confidential = self.page('confidential')
        self.assertIn('<span class="l">Cumulative oil</span><b>—<small>bbl</small></b><span class="d">Mar ’24 to May ’24 · <strong>0</strong> of 3 months known</span>', confidential)
        self.assertIn('<b>—<small>bbl/d</small></b><span class="d">no month with producing days</span>', confidential)
        self.assertIn('<span class="l">Oil, last 12 months</span><b>—<small>bbl</small></b><span class="d">0 of 3 months known</span>', confidential)
        self.assertIn('<dt>Unknown months</dt><dd>3 of 3', confidential)
        self.assertIn('<dt>First 3 months, oil</dt><dd>—</dd>', confidential)
        self.assertNotIn('<dt>Last month with oil</dt>', confidential)
        self.assertNotIn('<dt>Latest oil rate</dt>', confidential)
        self.assertIn('<path class="s oil" d="M58,134"/>', confidential)                          # all unknown: a baseline, nothing drawn as zero
        short = self.page('short')
        self.assertIn('<dt>Pools reported</dt><dd>MADISON, TYLER</dd>', short)
        self.assertIn('<p class="sub">3 months reported, Oct 2024 to Dec 2024, MADISON and TYLER pools.</p>', short)
        self.assertIn('<span class="l">Cumulative oil</span><b>840<small>bbl</small></b>', short)
        by_api = self.page('byApi')
        self.assertIn('<span class="l">Cumulative oil</span><b>90<small>bbl</small></b>', by_api)

    def test_well_without_production_says_so_without_zero(self):
        html = self.page('none')
        self.assertIn('does not appear on its own in the <a href="https://www.dmr.nd.gov/oilgas/mprindex.asp"', html)
        self.assertIn('Before Jul 2024 a well in a unitized pool was listed only inside its unit', html)
        self.assertIn('Jan 2024 to Dec 2024', html)
        self.assertNotIn('prodchart', html)
        self.assertNotIn('assets/production.js', html)

    def test_unit_pool_well_note(self):
        html = self.page('unitPool')
        self.assertIn('<span class="l">Cumulative oil</span><b>450<small>bbl</small></b>', html)
        self.assertIn('Before Jul 2024 this well', html)
        self.assertIn('reported only as a unit total, so the reports hold no figures for the well itself in those years; its own figures start in Jul 2024.', html)

    def test_compare_hooks(self):
        """The comparison block reads the pages themselves; the build only marks the unit list and loads the module."""
        html = self.page('full')                                          # the fixture's first well sits in unit dsu-fixture-a
        self.assertIn('<li data-acres="1280"><a href="/repo/units/dsu-fixture-a/">', html)
        self.assertIn('<script type="module" src="/repo/assets/compare.js">', html)
        self.assertNotIn('class="compare"', html)                         # nothing static: the module builds the block on request
        unit = (self.out / 'units' / 'dsu-fixture-a' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('<script type="module" src="/repo/assets/compare.js">', unit)
        self.assertIn('<td class="num">10,400 ft<small>100%</small></td>', unit)   # what the module reads for lateral length
        self.assertTrue((self.out / 'assets' / 'compare.js').exists())
        self.assertIn('assets/compare.js', self.page('none'))              # no production of its own, but a unit to compare
        no_units = next(p.read_text(encoding='utf-8') for p in (self.out / 'wells').glob('*/index.html') if 'Spacing units</h2>' not in p.read_text(encoding='utf-8'))
        self.assertNotIn('assets/compare.js', no_units)                    # nothing to compare with

    def test_build_without_feed_has_no_panels(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'site'
            report = bwp.build(self.source, out, 'https://example.github.io/repo/', limit=5)
            self.assertEqual(report['wellsWithProduction'], 0)
            self.assertIsNone(report['production'])
            page = next((out / 'wells').glob('*/index.html')).read_text(encoding='utf-8')
            self.assertNotIn('class="panel production"', page)
            self.assertNotIn('assets/compare.js', page)                  # no feed, nothing to compare
            self.assertIn('No production feed', bwp.markdown_summary(report))


if __name__ == '__main__':
    unittest.main()
