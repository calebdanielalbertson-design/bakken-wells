"""Offline tests for scripts/wells/redirects.py: the GitHub Pages copy forwarding to bakkensubsurface.com/wells/."""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells import build_well_pages as bwp  # noqa: E402
from wells import fixture  # noqa: E402
from wells import redirects as rd  # noqa: E402

TO = 'https://bakkensubsurface.com/wells'


class MappingTest(unittest.TestCase):
    def test_new_parts(self):
        self.assertEqual(rd.new_parts([]), [])
        self.assertEqual(rd.new_parts(['wells', '33-053-04567']), ['33-053-04567'])
        self.assertEqual(rd.new_parts(['counties', 'mckenzie']), ['counties', 'nd-mckenzie'])
        self.assertEqual(rd.new_parts(['townships', '153n-91w']), ['townships', 'nd-153n-91w'])
        self.assertEqual(rd.new_parts(['counties']), ['counties'])
        self.assertEqual(rd.new_parts(['operators', 'continental-resources-inc']), ['operators', 'continental-resources-inc'])
        self.assertEqual(rd.new_parts(['units', 'by-county', 'burke']), ['units', 'by-county', 'burke'])
        self.assertEqual(rd.url_for(TO, ['counties', 'nd-mckenzie']), 'https://bakkensubsurface.com/wells/counties/nd-mckenzie/')
        self.assertEqual(rd.url_for(TO + '/', []), 'https://bakkensubsurface.com/wells/')


class RedirectSiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        fixture.write_fixture(root / 'dmr', wells=60)
        kwargs = dict(data=root / 'dmr' / 'well-index-data.json.gz', production=root / 'dmr' / 'well-production.json.gz')
        cls.old, cls.new, cls.out = root / 'old', root / 'new', root / 'out'
        bwp.build(root / 'dmr', cls.old, 'https://example.github.io/bakken-wells', **kwargs)
        bwp.build(root / 'dmr', cls.new, TO, layout='site', **kwargs)
        # A page the new build no longer has: its stub must fall back to the page above it.
        gone = next(p for p in (cls.new / 'operators').iterdir() if p.is_dir())
        cls.gone = gone.name
        shutil.rmtree(gone)
        cls.counts = rd.write_redirects(cls.old, cls.out, TO, '/bakken-wells', cls.new, 'token123')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def forward(self, rel):
        page = (self.out / rel / 'index.html').read_text(encoding='utf-8')
        return re.search(r'<link rel="canonical" href="([^"]+)">', page).group(1), page

    def test_every_old_page_has_a_stub(self):
        old_pages = {p.relative_to(self.old).as_posix() for p in self.old.rglob('index.html')}
        stubs = {p.relative_to(self.out).as_posix() for p in self.out.rglob('index.html')}
        self.assertEqual(old_pages, stubs)
        self.assertEqual(self.counts['pages'], len(old_pages))
        self.assertEqual(self.counts['fellBack'], 1)
        self.assertFalse((self.out / 'assets').exists())          # scripts, styles and data are not carried over
        self.assertFalse(list(self.out.glob('sitemap*.xml')))

    def test_every_target_exists_in_the_new_build(self):
        for page in self.out.rglob('index.html'):
            url, _ = self.forward(page.parent.relative_to(self.out).as_posix())
            rel = url[len(TO):].strip('/')
            self.assertTrue((self.new / rel / 'index.html').is_file(), f'{page} -> {url}')

    def test_wells_counties_and_townships(self):
        well = next(p.name for p in (self.old / 'wells').iterdir() if p.is_dir())
        url, page = self.forward(f'wells/{well}')
        self.assertEqual(url, f'{TO}/{well}/')
        self.assertIn(f'<meta http-equiv="refresh" content="0; url={url}">', page)
        self.assertIn(f'location.replace("{url}"+location.search+location.hash)', page)
        self.assertIn('(moved)</title>', page)
        county = next(p.name for p in (self.old / 'counties').iterdir() if p.is_dir())
        self.assertEqual(self.forward(f'counties/{county}')[0], f'{TO}/counties/nd-{county}/')
        township = next(p.name for p in (self.old / 'townships').iterdir() if p.is_dir())
        self.assertEqual(self.forward(f'townships/{township}')[0], f'{TO}/townships/nd-{township}/')

    def test_fallback_home_and_404(self):
        self.assertEqual(self.forward(f'operators/{self.gone}')[0], f'{TO}/operators/')
        url, home = self.forward('.')
        self.assertEqual(url, f'{TO}/')
        self.assertIn('<meta name="google-site-verification" content="token123">', home)
        self.assertNotIn('google-site-verification', self.forward('counties')[1])
        not_found = (self.out / '404.html').read_text(encoding='utf-8')
        self.assertIn('"/bakken-wells"', not_found)
        self.assertIn('"nd-"+s[1]', not_found)
        self.assertIn('noindex', not_found)
        self.assertEqual((self.out / 'robots.txt').read_text(encoding='utf-8'), 'User-agent: *\nAllow: /\n')
        self.assertTrue((self.out / '.nojekyll').is_file())


if __name__ == '__main__':
    unittest.main()
