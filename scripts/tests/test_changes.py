"""Offline tests for scripts/wells/changes.py: the day-by-day change feed at bakkensubsurface.com/wells/changes/."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells import build_well_pages as bwp  # noqa: E402
from wells import changes as chg  # noqa: E402
from wells import fixture  # noqa: E402

FIELDS = ['name', 'api', 'file', 'op', 'county', 'status', 'type', 'lat', 'lon', 'slug']


def index(wells, operators=('Alpha Oil', 'Beta Energy'), statuses=('A', 'LOC', 'PA', 'Producing')):
    return {'fields': FIELDS, 'operators': list(operators), 'counties': ['McKenzie', 'Richland'], 'countyStates': ['ND', 'MT'],
            'statuses': list(statuses), 'types': ['OG'], 'statusGloss': {'A': 'Active', 'LOC': 'Permitted location, not yet drilled', 'PA': 'Plugged and abandoned'},
            'wells': wells}


BEFORE = index([
    ['ONE 1H', '3305300001', 101, 0, 0, 1, 0, 47.1, -103.1, None],   # permitted -> active
    ['TWO 2H', '3305300002', 102, 0, 0, 0, 0, 47.2, -103.2, None],   # operator Alpha -> Beta
    ['THREE 3H', '3305300003', 103, 0, 0, 0, 0, 47.3, -103.3, None],  # renamed
    ['FOUR 4H', '3305300004', 104, 0, 0, 2, 0, 47.4, -103.4, None],   # gone
    ['Vandal 1-16H', '2508322812', None, 1, 1, 3, 0, 47.6, -104.4, None],
])
AFTER = index([
    ['ONE 1H', '3305300001', 101, 0, 0, 0, 0, 47.1, -103.1, None],
    ['TWO 2H', '3305300002', 102, 1, 0, 0, 0, 47.2, -103.2, None],
    ['THREE 3H REDRILL', '3305300003', 103, 0, 0, 0, 0, 47.3, -103.3, None],
    ['FIVE 5H', '3305300005', 105, 1, 0, 1, 0, 47.5, -103.5, None],   # new permit
    ['Vandal 1-16H', '2508322812', None, 1, 1, 3, 0, 47.6, -104.4, None],
])


class DiffTest(unittest.TestCase):
    def test_each_kind_of_change(self):
        events = chg.diff(BEFORE, AFTER)
        kinds = [(e['type'], e['slug']) for e in events]
        self.assertEqual(kinds, [('new', '33-053-00005'), ('status', '33-053-00001'), ('operator', '33-053-00002'),
                                 ('renamed', '33-053-00003'), ('removed', '33-053-00004')])
        status = events[1]
        self.assertEqual((status['from'], status['to'], status['state'], status['county']),
                         ('Permitted location, not yet drilled', 'Active', 'ND', 'McKenzie'))
        self.assertEqual((events[2]['from'], events[2]['to']), ('Alpha Oil', 'Beta Energy'))
        self.assertEqual(events[0]['status'], 'Permitted location, not yet drilled')
        self.assertEqual(events[4]['name'], 'FOUR 4H')
        self.assertEqual(chg.diff(None, AFTER), [])          # nothing to compare with: no events
        self.assertEqual(chg.diff(AFTER, AFTER), [])
        mt = copy.deepcopy(AFTER)
        mt['wells'][4][5] = 2                                 # a Montana status keeps the Board's own words
        self.assertEqual(chg.diff(AFTER, mt)[0]['to'], 'PA')


class FeedTest(unittest.TestCase):
    def test_days_merge_trim_and_notes(self):
        first = chg.update(None, [], day='2026-11-26', production={'ND': '2026-08', 'MT': '2026-08'}, had_previous=False)
        self.assertEqual(first['days'][0]['notes'][0]['type'], 'start')
        self.assertEqual(first['productionThrough'], {'ND': '2026-08', 'MT': '2026-08'})
        events = chg.diff(BEFORE, AFTER)
        feed = chg.update(first, events, day='2026-11-27', production={'ND': '2026-09', 'MT': '2026-08'}, had_previous=True)
        self.assertEqual([d['date'] for d in feed['days']], ['2026-11-27', '2026-11-26'])
        today = feed['days'][0]
        self.assertEqual(today['counts'], {'new': 1, 'status': 1, 'operator': 1, 'renamed': 1, 'removed': 1})
        self.assertEqual(today['total'], 5)
        self.assertEqual(today['notes'], [{'type': 'production', 'state': 'ND', 'from': '2026-08', 'to': '2026-09'}])
        again = chg.update(feed, events[:2], day='2026-11-27', production={'ND': '2026-09'}, had_previous=True)
        self.assertEqual(again['days'][0]['total'], 5)        # a same-day rerun merges, without doubles
        self.assertEqual(len(again['days']), 2)
        long = feed
        for n in range(60):
            long = chg.update(long, [], day=f'2027-{1 + n // 28:02d}-{1 + n % 28:02d}', production={}, had_previous=True)
        self.assertEqual(len(long['days']), chg.KEEP_DAYS)
        self.assertEqual(long['days'][0]['date'], '2027-03-04')

    def test_page_and_atom(self):
        feed = chg.update(None, chg.diff(BEFORE, AFTER), day='2026-11-27', production={}, had_previous=True)
        body = chg.page_body(feed, well_root='/wells')
        self.assertIn('<h2>November 27, 2026</h2>', body)
        self.assertIn('href="/wells/33-053-00005/"', body)
        self.assertIn('Permitted location, not yet drilled → Active', body)
        root = ET.fromstring(chg.atom(feed, well_root_url='https://bakkensubsurface.com/wells'))
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('a:entry', ns)
        self.assertEqual(len(entries), 1)
        self.assertIn('1 new wells and permits', entries[0].find('a:title', ns).text)


class BuildTest(unittest.TestCase):
    def test_build_writes_the_feed_and_compares_with_the_live_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture.write_fixture(root / 'dmr', wells=60)
            out = root / 'site'
            report = bwp.build(root / 'dmr', out, 'https://bakkensubsurface.com/wells', layout='site')
            feed = json.loads((out / 'changes' / 'index.json').read_text(encoding='utf-8'))
            self.assertEqual(feed['days'][0]['notes'][0]['type'], 'start')
            self.assertFalse(report['changes']['comparedWithLive'])
            self.assertIn('href="/wells/changes/"', (out / 'index.html').read_text(encoding='utf-8'))
            # pretend yesterday's index had one well fewer and another well under a different status
            live = json.loads((out / 'index.json').read_text(encoding='utf-8'))
            gone = live['wells'].pop(0)
            live['statuses'].append('XYZ')
            live['wells'][0][5] = len(live['statuses']) - 1
            (root / 'live-index.json').write_text(json.dumps(live), encoding='utf-8')
            (root / 'live-changes.json').write_text(json.dumps(feed), encoding='utf-8')
            report = bwp.build(root / 'dmr', out, 'https://bakkensubsurface.com/wells', layout='site',
                               previous_index=root / 'live-index.json', previous_changes=root / 'live-changes.json')
            feed = json.loads((out / 'changes' / 'index.json').read_text(encoding='utf-8'))
            self.assertTrue(report['changes']['comparedWithLive'])
            today = feed['days'][0]
            self.assertEqual(today['counts']['new'], 1)
            self.assertEqual(today['counts']['status'], 1)
            self.assertEqual(today['changes'][0]['name'], gone[0])
            html = (out / 'changes' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('<!--site-header-->', html)
            ET.fromstring((out / 'changes' / 'feed.xml').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
