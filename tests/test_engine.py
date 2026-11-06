import unittest
import numpy as np
from sedimentcfd.engine import Config, Gabion, simulate, solve_flow


class EngineTests(unittest.TestCase):
    def base(self, **kwargs):
        return Config(**(dict(nx=24, ny=12, duration=40, dt=2, gabions=[]) | kwargs))

    def test_uniform_flow_analytic_solution(self):
        c = self.base()
        f = solve_flow(c)
        expected = c.velocity * c.drag / 9.81 * (c.length-f['points'][:, 0])
        np.testing.assert_allclose(f['head'], expected, atol=1e-12)
        np.testing.assert_allclose(f['velocity'][:, 0], c.velocity, atol=1e-11)
        np.testing.assert_allclose(f['velocity'][:, 1], 0, atol=1e-11)
        self.assertAlmostEqual(np.maximum(f['boundary'], 0).sum(), c.velocity*c.depth*c.width, places=10)

    def test_still_water_matches_backward_euler_settling(self):
        c = self.base(velocity=0, initial_concentration=.2, settling_velocity=.01)
        r = simulate(c)
        expected = .2 / (1+c.dt*.01/c.depth)**int(c.duration/c.dt)
        np.testing.assert_allclose(r['frames'][-1]['concentration'], expected, rtol=1e-12)
        self.assertLess(r['summary']['relative_mass_error'], 1e-12)
        exact = .2*np.exp(-.01*c.duration/c.depth)
        fine = simulate(self.base(velocity=0, initial_concentration=.2, settling_velocity=.01, dt=1))
        self.assertLess(abs(fine['frames'][-1]['concentration'][0]-exact), abs(expected-exact))

    def test_no_deposition_uniform_concentration(self):
        c = self.base(settling_velocity=0, initial_concentration=.15)
        r = simulate(c)
        np.testing.assert_allclose(r['frames'][-1]['concentration'], .15, atol=1e-12)
        self.assertEqual(r['summary']['deposited_kg'], 0)
        self.assertLess(r['summary']['relative_mass_error'], 1e-12)

    def test_full_width_barrier_series_resistance(self):
        c = self.base(nx=40, ny=16, gabions=[Gabion(20, 8, 16, 4, 90, 20)])
        f = solve_flow(c)
        # Series resistance: Q/Q0 = L / (L - b + R*b).
        expected = c.velocity*c.depth*c.width*c.length/(c.length-4+20*4)
        self.assertAlmostEqual(np.maximum(f['boundary'], 0).sum(), expected, places=9)

    def test_angled_gabion_deflects_and_slows(self):
        f = solve_flow(self.base(nx=60, ny=24, gabions=[Gabion(18,8,9,2,60,30)]))
        speed = np.linalg.norm(f['element_velocity'], axis=1)
        self.assertLess(speed[f['resistance']>1].mean(), speed[f['resistance']==1].mean()*.5)
        self.assertGreater(np.abs(f['velocity'][:,1]).max(), .05)

    def test_transient_positivity_budget_and_exact_end_time(self):
        r = simulate(self.base(gabions=[Gabion(18,8,9,2,60,30)], duration=47, dt=3))
        self.assertEqual(r['history'][-1]['time'], 47)
        self.assertLess(r['summary']['relative_mass_error'], 1e-11)
        self.assertGreater(r['summary']['deposited_kg'], 0)
        for frame in r['frames']:
            self.assertGreaterEqual(min(frame['concentration']), -1e-13)
            self.assertLessEqual(max(frame['concentration']), .15+1e-12)
        self.assertTrue(np.all(np.diff([h['deposited_kg'] for h in r['history']])>=0))

    def test_zero_sediment_stays_zero(self):
        r = simulate(self.base(inlet_concentration=0))
        self.assertEqual(r['summary']['deposited_kg'], 0)
        self.assertEqual(r['summary']['suspended_kg'], 0)

    def test_invalid_parameters(self):
        for values in [{'depth':0}, {'bed_porosity':1}, {'nx':2.5}, {'dt':0}, {'velocity':float('nan')}, {'diffusion':-1}, {'gabions':[{'resistance':0}]}, {'duration':1e8}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config.from_dict(values)

    def test_unresolved_gabion_warning(self):
        r = simulate(self.base(gabions=[Gabion(20,8,.00001,.00001)]))
        self.assertTrue(any('未覆盖' in w for w in r['warnings']))


if __name__ == '__main__':
    unittest.main()
