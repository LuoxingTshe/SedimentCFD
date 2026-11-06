import unittest
from sedimentcfd import Config
from sedimentcfd.thetis_backend import ThetisSettings, depth_integrated_volume, environment_report

class AdapterTests(unittest.TestCase):
    def test_concentration_units(self):
        # A 2 m water column at 0.265 kg/m³ carries 0.53 kg/m².
        q = depth_integrated_volume(.265, 2, 2650)
        self.assertAlmostEqual(q, .0002)
        self.assertAlmostEqual(q*2650, .53)

    def test_default_settings(self):
        ThetisSettings().validate(Config())

    def test_reject_incompatible_physics(self):
        for s, c in [(ThetisSettings(grain_size=1e-9), Config()),
                     (ThetisSettings(molecular_viscosity=.01), Config()),
                     (ThetisSettings(timestep=0), Config()),
                     (ThetisSettings(), Config(velocity=0)),
                     (ThetisSettings(), Config(sediment_density=900))]:
            with self.subTest(s=s,c=c), self.assertRaises(ValueError):
                s.validate(c)

    def test_presence_is_not_runtime_validation(self):
        report = environment_report()
        self.assertFalse(report['runtime_validated'])
        self.assertEqual(report['dependencies_present'], all(report['modules'].values()))
