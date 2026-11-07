"""Tests for scripts/wells/changed.py: when a scheduled rebuild may be skipped."""
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells import changed  # noqa: E402

LIVE = {'archives': [{'kind': 'wells', 'file': 'OGD_Wells.zip', 'sha256': 'a' * 64}, {'kind': 'permits', 'file': 'OGD_PermitStatusBeforeSpud.zip', 'sha256': 'b' * 64}],
        'data': {'generated': '2026-11-07T04:44:11Z'}, 'generator': 'c0ffee' * 6 + 'c0ff'}
PROVENANCE = {'archives': [{'kind': 'wells', 'file': 'OGD_Wells.zip', 'sha256': 'a' * 64}, {'kind': 'permits', 'file': 'OGD_PermitStatusBeforeSpud.zip', 'sha256': 'b' * 64}]}


class CompareTests(unittest.TestCase):
    def test_identical_inputs_are_unchanged(self):
        unchanged, lines = changed.compare(LIVE, PROVENANCE, '2026-11-07T04:44:11Z', LIVE['generator'])
        self.assertTrue(unchanged)
        self.assertEqual(len(lines), 4)

    def test_any_difference_is_a_change(self):
        new_wells = {'archives': [dict(PROVENANCE['archives'][0], sha256='d' * 64), PROVENANCE['archives'][1]]}
        self.assertFalse(changed.compare(LIVE, new_wells, '2026-11-07T04:44:11Z', LIVE['generator'])[0])
        self.assertFalse(changed.compare(LIVE, PROVENANCE, '2026-11-08T00:00:00Z', LIVE['generator'])[0])
        self.assertFalse(changed.compare(LIVE, PROVENANCE, None, LIVE['generator'])[0])          # data asset gone
        self.assertFalse(changed.compare(LIVE, PROVENANCE, '2026-11-07T04:44:11Z', 'deadbeef')[0])
        self.assertFalse(changed.compare({}, PROVENANCE, None, None)[0])                        # no live record yet
        self.assertFalse(changed.compare(LIVE, {'archives': []}, None, None)[0])
        without_generator = dict(LIVE)
        del without_generator['generator']
        self.assertFalse(changed.compare(without_generator, PROVENANCE, '2026-11-07T04:44:11Z', 'abc')[0])   # older live build.json
        self.assertTrue(changed.compare(without_generator, PROVENANCE, '2026-11-07T04:44:11Z', None)[0])    # generator not supplied: not compared

    def test_cli_writes_github_output_and_never_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'live.json').write_text(json.dumps(LIVE), encoding='utf-8')
            (root / 'provenance.json').write_text(json.dumps(PROVENANCE), encoding='utf-8')
            with gzip.open(root / 'data.json.gz', 'wt', encoding='utf-8') as handle:
                json.dump({'version': 1, 'generated': '2026-11-07T04:44:11Z', 'units': [], 'orders': []}, handle)
            out = root / 'out.txt'
            code = changed.main(['--live', str(root / 'live.json'), '--provenance', str(root / 'provenance.json'), '--data', str(root / 'data.json.gz'),
                                 '--generator', LIVE['generator'], '--github-output', str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(out.read_text(encoding='utf-8'), 'unchanged=true\n')
            # an unreachable live record means "build", and still exits 0
            code = changed.main(['--live', str(root / 'missing.json'), '--provenance', str(root / 'provenance.json'), '--github-output', str(out)])
            self.assertEqual(code, 0)
            self.assertTrue(out.read_text(encoding='utf-8').endswith('unchanged=false\n'))


if __name__ == '__main__':
    unittest.main()
