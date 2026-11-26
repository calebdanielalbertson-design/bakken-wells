"""Offline tests for scripts/wells/publish_kv.py and the --layout site build it publishes (bakkensubsurface.com/wells/)."""
from __future__ import annotations

import base64
import gzip
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells import build_well_pages as bwp  # noqa: E402
from wells import fixture  # noqa: E402
from wells import publish_kv as pkv  # noqa: E402


class FakeKV:
    """Just enough of the Cloudflare KV REST API: bulk write, bulk delete, read one value."""

    def __init__(self):
        self.store, self.calls = {}, []

    def __call__(self, request, timeout=None):
        url = urllib.parse.urlsplit(request.full_url)
        path = url.path.split('/storage/kv/namespaces/', 1)[1].split('/', 1)[1]
        self.calls.append((request.get_method(), path.split('/')[0] if path.startswith('values') else path))
        assert request.get_header('Authorization') == 'Bearer token'
        if request.get_method() == 'GET' and path.startswith('values/'):
            key = urllib.parse.unquote(path[len('values/'):])
            if key not in self.store:
                raise urllib.error.HTTPError(request.full_url, 404, 'not found', {}, io.BytesIO(b'{}'))
            return io.BytesIO(self.store[key][0])
        body = json.loads(request.data)
        if request.get_method() == 'PUT' and path == 'bulk':
            for item in body:
                self.store[item['key']] = (base64.b64decode(item['value']), item['metadata'])
            return io.BytesIO(json.dumps({'success': True, 'result': {'successful_key_count': len(body), 'unsuccessful_keys': []}}).encode())
        if request.get_method() == 'POST' and path == 'bulk/delete':
            for key in body:
                self.store.pop(key, None)
            return io.BytesIO(json.dumps({'success': True, 'result': {'successful_key_count': len(body), 'unsuccessful_keys': []}}).encode())
        raise AssertionError(f'unexpected {request.get_method()} {path}')


class SiteLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        fixture.write_fixture(root / 'dmr', wells=120)
        cls.out = root / 'site'
        cls.report = bwp.build(root / 'dmr', cls.out, 'https://bakkensubsurface.com/wells', layout='site',
                               data=root / 'dmr' / 'well-index-data.json.gz', production=root / 'dmr' / 'well-production.json.gz')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_wells_sit_directly_under_the_mount(self):
        wells = [p for p in self.out.iterdir() if p.is_dir() and p.name.startswith('33-')]
        self.assertTrue(wells)
        self.assertFalse((self.out / 'wells').exists())
        page = (wells[0] / 'index.html').read_text(encoding='utf-8')
        self.assertIn(f'<link rel="canonical" href="https://bakkensubsurface.com/wells/{wells[0].name}/">', page)
        self.assertEqual(page.count('<!--site-header-->'), 1)
        self.assertEqual(page.count('<!--site-footer-->'), 1)
        self.assertIn('<script defer src="/visitor-counts.js"></script>', page)
        self.assertNotIn('google-site-verification', page)
        self.assertNotIn('href="/wells/wells/', page)

    def test_counties_and_townships_carry_the_state(self):
        self.assertTrue(all(p.name.startswith('nd-') for p in (self.out / 'counties').iterdir() if p.is_dir()))
        self.assertTrue(all(p.name.startswith('nd-') for p in (self.out / 'townships').iterdir() if p.is_dir()))
        search = (self.out / 'index.html').read_text(encoding='utf-8')
        self.assertIn('fetch("/wells/index.json")', search)
        self.assertIn('wellRoot:"/wells",townshipPrefix:"nd-"', search)
        for name in ('.nojekyll', 'CNAME', 'robots.txt'):
            self.assertFalse((self.out / name).exists(), name)
        self.assertEqual(self.report['layout'], 'site')

    def test_keys(self):
        entries, mount = pkv.collect(self.out)
        self.assertEqual(mount, '/wells')
        self.assertIn('/wells/', entries)
        self.assertIn('/wells/index.json', entries)
        self.assertIn('/wells/404.html', entries)
        self.assertIn('/wells/assets/well.css', entries)
        self.assertTrue(any(k.startswith('/wells/33-') and k.endswith('/') for k in entries))
        self.assertTrue(all(k.startswith('/wells/') for k in entries))
        self.assertEqual(entries['/wells/assets/well.css']['t'], 'text/css; charset=utf-8')
        self.assertEqual(pkv.key_for('townships/nd-153n-91w/index.html', '/wells'), '/wells/townships/nd-153n-91w/')

    def test_a_pages_build_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'build.json').write_text(json.dumps({'layout': 'pages', 'basePath': '/bakken-wells'}))
            with self.assertRaises(pkv.PublishError):
                pkv.collect(Path(tmp))

    def test_folder_stand_in(self):
        entries, _ = pkv.collect(self.out)
        with tempfile.TemporaryDirectory() as tmp:
            count = pkv.to_folder(entries, Path(tmp) / 'kv')
            keys = json.loads((Path(tmp) / 'kv' / 'keys.json').read_text(encoding='utf-8'))
            self.assertEqual(count, len(entries))
            entry = keys['/wells/']
            body = gzip.decompress((Path(tmp) / 'kv' / 'values' / entry['f']).read_bytes())
            self.assertEqual(body, (self.out / 'index.html').read_bytes())

    def test_upload_sends_only_what_changed(self):
        entries, _ = pkv.collect(self.out)
        fake = FakeKV()
        kv = pkv.KV('acct', 'ns', 'token', opener=fake, retries=0)
        first = pkv.publish(entries, kv, log=lambda *_: None)
        self.assertEqual(first['write'], len(entries))
        self.assertIn(pkv.MANIFEST_KEY, fake.store)
        stored, metadata = fake.store['/wells/assets/well.css']
        self.assertEqual(gzip.decompress(stored), (self.out / 'assets' / 'well.css').read_bytes())
        self.assertEqual(metadata, {'t': 'text/css; charset=utf-8', 'h': entries['/wells/assets/well.css']['h']})

        again = pkv.publish(entries, kv, log=lambda *_: None)
        self.assertEqual((again['write'], again['delete']), (0, 0))

        changed = dict(entries)
        gone = next(k for k in changed if k.startswith('/wells/33-'))
        del changed[gone]
        with tempfile.TemporaryDirectory() as tmp:
            edited = Path(tmp) / 'well.css'
            edited.write_text('body{}', encoding='utf-8')
            changed['/wells/assets/well.css'] = {'path': edited, 't': 'text/css; charset=utf-8', 'h': 'feedfacefeedface'}
            third = pkv.publish(changed, kv, log=lambda *_: None)
        self.assertEqual((third['write'], third['delete']), (1, 1))
        self.assertNotIn(gone, fake.store)

    def test_dry_run_writes_nothing(self):
        entries, _ = pkv.collect(self.out)
        fake = FakeKV()
        result = pkv.publish(entries, pkv.KV('acct', 'ns', 'token', opener=fake, retries=0), dry_run=True, log=lambda *_: None)
        self.assertEqual(result['write'], len(entries))
        self.assertEqual(fake.store, {})


if __name__ == '__main__':
    unittest.main()
