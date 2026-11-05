from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReferenceBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = (ROOT / 'vendor/haloflux/lab.html').read_text(encoding='utf-8')
        cls.runtime = (ROOT / 'runtime.js').read_text(encoding='utf-8')

    def test_outer_beam_geometry_is_preserved(self):
        for start, end in [('function makeProfile(n)', 'function buildLanes()')]:
            upstream = self.upstream.split(start, 1)[1].split(end, 1)[0]
            built = self.runtime.split(start, 1)[1].split(end, 1)[0]
            self.assertEqual(built, upstream)

    def test_original_default_parameters_are_unchanged(self):
        pattern = r'const BAKED_MINE = \{[^\n]+\};'
        self.assertEqual(re.search(pattern, self.upstream).group(), re.search(pattern, self.runtime).group())

    def test_synthetic_agent_kpis_and_events_are_removed(self):
        self.assertNotIn('function kpiTick()', self.runtime)
        self.assertNotIn('function feedPush()', self.runtime)
        self.assertNotIn("const AG = Array.from({ length: 12 }", self.runtime)


if __name__ == '__main__':
    unittest.main()
