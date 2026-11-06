"""End-to-end smoke test of the real solver. Skipped unless Firedrake + Thetis import."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from sedimentcfd import Config, Gabion

HAVE_THETIS = all(importlib.util.find_spec(m) for m in ('firedrake', 'thetis'))


@unittest.skipUnless(HAVE_THETIS, 'Firedrake/Thetis not installed (source activate_thetis.sh)')
class ThetisSmokeTest(unittest.TestCase):
    def test_short_run_writes_preview(self):
        from sedimentcfd.preview import build_preview
        config = Config(length=4, width=2, nx=8, ny=4, spinup=10, duration=10, timestep=0.25,
                        gabions=[Gabion(2, 1, 1, 0.5, 30, 10)])
        with tempfile.TemporaryDirectory() as tmp:
            data = build_preview(config, Path(tmp))
            self.assertTrue((Path(tmp) / 'index.html').is_file())
            self.assertEqual(json.loads((Path(tmp) / 'preview_data.json').read_text())['config'], config.to_dict())
        self.assertEqual(len(data['frames']), 41)
        self.assertEqual(len(data['triangles']), 2 * 8 * 4)
        self.assertGreater(data['numerics']['min_depth'], 0)
        self.assertGreaterEqual(data['numerics']['min_concentration'], -1e-3 * data['numerics']['max_concentration'])
        self.assertGreater(data['closure']['dstar'], 1)
        self.assertGreater(data['inventory'][0]['suspended_kg'], 0)


if __name__ == '__main__':
    unittest.main()
