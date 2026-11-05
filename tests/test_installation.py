"""Clean-checkout install regressions: no publisher paths, screenshots or token."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_wallpaper
import server
from server import CodexAdapter, DSHAdapter

ROOT = Path(__file__).resolve().parents[1]


class DataDirectoryTests(unittest.TestCase):
    def test_custom_sources_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            with patch.dict(os.environ, {'CODEX_HOME': str(root / 'custom-codex'), 'DSH_HOME': str(root / 'custom-dsh')}):
                self.assertEqual(CodexAdapter().home, root / 'custom-codex')
                self.assertEqual(DSHAdapter().home, root / 'custom-dsh')
                self.assertEqual(CodexAdapter(root / 'override').home, root / 'override')
                self.assertEqual(DSHAdapter(root / 'override').home, root / 'override')


class CleanWallpaperInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='AGT clean install ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkout = self.root / 'download'
        self.checkout.mkdir()
        for name in ('index.html', 'runtime.js', 'screen.css', 'UPSTREAM.txt'):
            shutil.copy2(ROOT / name, self.checkout / name)
        for name in ('fonts', 'haloflux'):
            shutil.copytree(ROOT / 'vendor' / name, self.checkout / 'vendor' / name)
        self.engine = self.root / 'Steam library' / 'wallpaper_engine'
        self.engine.mkdir(parents=True)
        (self.engine / 'wallpaper64.exe').touch()
        self.root_patch = patch.object(install_wallpaper, 'ROOT', self.checkout)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_fresh_install_without_local_artifacts_and_repeat_install(self):
        self.assertFalse((self.checkout / 'artifacts').exists())
        self.assertFalse((self.checkout / 'wallpaper').exists())
        target = install_wallpaper.install(self.engine)
        self.assertTrue((target / 'preview.png').is_file())
        self.assertTrue((target / 'fonts/OFL.txt').is_file())
        self.assertEqual((target / 'LICENSE').read_bytes(), (ROOT / 'vendor/haloflux/LICENSE').read_bytes())
        token = (self.checkout / '.wallpaper-token').read_text()
        self.assertEqual(len(token), 64)
        self.assertIn(token, (target / 'connection.js').read_text())
        html = (target / 'index.html').read_text(encoding='utf-8')
        self.assertNotIn('<iframe', html)
        self.assertIn('src="connection.js"', html)
        self.assertIn('src="runtime.js', html)
        definition = json.loads((target / 'project.json').read_text(encoding='utf-8'))
        self.assertIn('agt-command-center', definition['description'])
        self.assertFalse((self.engine / 'config.json').exists(), 'Installer must not set the active desktop')
        self.assertEqual(install_wallpaper.install(self.engine), target)
        self.assertEqual((self.checkout / '.wallpaper-token').read_text(), token)

    def test_unrelated_project_is_preserved(self):
        target = self.engine / 'projects/myprojects/AGT-Live-Command'
        target.mkdir(parents=True)
        original = '{"description":"Someone else project"}'
        (target / 'project.json').write_text(original)
        with self.assertRaises(ValueError):
            install_wallpaper.install(self.engine)
        self.assertEqual((target / 'project.json').read_text(), original)
        self.assertFalse((self.checkout / '.wallpaper-token').exists())

    def test_nonempty_unmarked_folder_is_preserved(self):
        target = self.engine / 'projects/myprojects/AGT-Live-Command'
        target.mkdir(parents=True)
        (target / 'index.html').write_text('user-owned content')
        with self.assertRaises(ValueError):
            install_wallpaper.install(self.engine)
        self.assertEqual((target / 'index.html').read_text(), 'user-owned content')


class LateWallpaperInstallTests(unittest.TestCase):
    def test_installing_or_rotating_token_after_server_start(self):
        with tempfile.TemporaryDirectory() as base, patch.object(server, 'ROOT', Path(base)):
            collector = server.Collector(lambda: {'codex': False, 'dsh': False})
            collector.poll()
            http = ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(collector))
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            url = f'http://127.0.0.1:{http.server_address[1]}/api/state'

            def request(token):
                return urlopen(Request(url, headers={'Origin': 'null', 'X-AGT-Token': token}), timeout=5)

            try:
                with self.assertRaises(HTTPError) as error:
                    request('fresh-token')
                self.assertEqual(error.exception.code, 403)
                token_file = Path(base) / '.wallpaper-token'
                token_file.write_text('fresh-token', encoding='ascii')
                with request('fresh-token') as response:
                    self.assertEqual(response.status, 200)
                token_file.write_text('rotated-token', encoding='ascii')
                with self.assertRaises(HTTPError) as error:
                    request('fresh-token')
                self.assertEqual(error.exception.code, 403)
                with request('rotated-token') as response:
                    self.assertEqual(response.status, 200)
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
