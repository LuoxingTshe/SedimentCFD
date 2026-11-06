import json
import unittest

from sedimentcfd import Config, Gabion
from sedimentcfd.__main__ import main
from sedimentcfd.model import sediment_q


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid_and_round_trip(self):
        config = Config()
        config.validate()
        again = Config.from_dict(json.loads(json.dumps(config.to_dict())))
        self.assertEqual(again, config)
        self.assertIsInstance(again.gabions[0], Gabion)

    def test_partial_dict_uses_defaults(self):
        config = Config.from_dict({'velocity': 0.5, 'gabions': []})
        self.assertEqual(config.velocity, 0.5)
        self.assertEqual(config.depth, Config().depth)
        self.assertEqual(config.gabions, [])

    def test_rejects_bad_input(self):
        bad = [
            {'unknown': 1},
            {'velocity': 0},
            {'depth': float('nan')},
            {'nx': 3.5},
            {'nx': True},
            {'bed_porosity': 1},
            {'sediment_density': 900},
            {'grain_size': 0.02e-3},  # D* < 1
            {'reference_height': 0.7},  # above water depth
            {'inlet_concentration': -1},
            {'gabions': [{'x': 99, 'y': 1, 'length': 1, 'width': 1}]},
            {'gabions': [{'x': 1, 'y': 1, 'length': 1, 'width': 1, 'resistance': 0.5}]},
            {'gabions': [{'x': 1, 'y': 1}]},
            {'gabions': 'none'},
            [],
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                Config.from_dict(data)

    def test_derived_quantities(self):
        config = Config(velocity=0.5, depth=0.4, width=10)
        self.assertAlmostEqual(config.discharge, 2.0)
        self.assertAlmostEqual(Config(grain_size=1e-3).dstar, 25.3, places=1)
        self.assertAlmostEqual(sediment_q(2650.0, 0.5, 2650.0), 0.5)

    def test_cli_prints_default_config(self):
        from contextlib import redirect_stdout
        import io
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(['config']), 0)
        self.assertEqual(Config.from_dict(json.loads(out.getvalue())), Config())


if __name__ == '__main__':
    unittest.main()
