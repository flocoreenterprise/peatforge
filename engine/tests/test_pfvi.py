import json
import math
import unittest
from dataclasses import asdict

from engine.pfvi import (BOUNDS, PFVIParams, calibrate, diobs, equilibrium_pfvi,
                         level, rainfall_factor, run_pfvi, water_table_factor, load_pfvi_config)


class PFVITests(unittest.TestCase):
    def test_hand_computed_three_day_and_previous_rain(self):
        # At this temperature 0.4982*exp(0.0905*T+1.6096)=4.268,
        # hence DF=0. At surface theta=1 hence WTF=aH=1.
        temperature = (math.log(4.268 / 0.4982) - 1.6096) / 0.0905
        result = run_pfvi([0]*3, [10, 2, 0], [temperature]*3,
                          params=PFVIParams(aH=1, bH=.01), initial_pfvi=100)
        for point, expected in zip(result['items'], [94.1, 91.1, 90.1]):
            self.assertAlmostEqual(point['pfvi'], expected)
        self.assertEqual([p['rf'] for p in result['items']], [4.9, 2, 0])
        self.assertEqual([p['previous_rain_mm'] for p in result['items']], [None, 10, 2])
        continued = run_pfvi([0], [2], [temperature], params=PFVIParams(aH=1, bH=.01),
                             initial_pfvi=94.1, previous_rain_mm=10)
        self.assertAlmostEqual(continued['items'][0]['pfvi'], 91.1)

    def test_rf_all_branches_and_threshold(self):
        cases = [(4, None, 0), (5.1, None, 0), (10, None, 4.9),
                 (10, 5.1, 4.9), (2, 5.10001, 2), (0, 10, 0)]
        for rain, previous, expected in cases:
            self.assertAlmostEqual(rainfall_factor(rain, previous), expected)

    def test_clamps_and_levels(self):
        dry = run_pfvi([1000]*3, [0]*3, [65]*3, initial_pfvi=1000)
        wet = run_pfvi([0]*3, [1000]*3, [28]*3, initial_pfvi=-100)
        self.assertTrue(all(0 <= p['pfvi'] <= 300 for r in (dry, wet) for p in r['items']))
        self.assertEqual(wet['items'][-1]['pfvi'], 0)
        self.assertEqual(dry['items'][-1]['pfvi'], 300)
        self.assertEqual([level(x) for x in [0,99.9,100,174.9,175,249.9,250,300]],
                         ['RENDAH','RENDAH','SEDANG','SEDANG','TINGGI','TINGGI','EKSTREM','EKSTREM'])

    def test_yaml_config_matches_default_assumptions(self):
        config = load_pfvi_config()
        self.assertEqual(config['params'], PFVIParams())
        self.assertEqual((config['fc'], config['sat']), (15, 45))

    def test_mineral_soil_diobs(self):
        self.assertEqual([diobs(x) for x in [0,15,30,45,70]], [300,300,150,0,0])
        with self.assertRaises(ValueError):
            diobs(None)
        with self.assertRaises(ValueError):
            diobs(30, fc=45, sat=15)

    def test_retention_and_team_equilibrium_prior(self):
        self.assertEqual(water_table_factor(-10), water_table_factor(0))
        self.assertGreater(water_table_factor(40), water_table_factor(60))
        self.assertAlmostEqual(equilibrium_pfvi(40), 175)
        self.assertAlmostEqual(equilibrium_pfvi(60), 250)
        self.assertTrue(math.isfinite(water_table_factor(1e200)))

    def test_bad_input_rejected(self):
        for kwargs in ({'rain_mm': [None]}, {'rain_mm': [-1]}, {'temperature_c': [100]},
                       {'wt_cm': [math.nan]}, {'initial_pfvi': math.inf},
                       {'params': PFVIParams(n=.5)}, {'annual_rain_mm': -1}):
            inputs = dict(wt_cm=[40], rain_mm=[0], temperature_c=[28])
            inputs.update(kwargs)
            with self.assertRaises(ValueError):
                run_pfvi(**inputs)

    def test_calibration_two_starts_bounded_finite_deterministic(self):
        args = ([30,35,40,45,50,55], [0,2,0,10,0,0], [28]*6, [33,32,31,32,30,29])
        fitted = calibrate(*args, maxiter=100)
        repeated = calibrate(*args, maxiter=100)
        self.assertEqual(fitted, repeated)
        self.assertEqual(fitted['starts'], 2)
        for value, (low, high) in zip(fitted['params'].values(), BOUNDS):
            self.assertTrue(low <= value <= high)
        self.assertTrue(math.isfinite(fitted['objective']))
        json.dumps(fitted, allow_nan=False)
        self.assertTrue(any('not independent validation' in a for a in fitted['assumptions']))
        with self.assertRaises(ValueError):
            calibrate([40,40],[0,0],[28,28],[None,30])


if __name__ == '__main__':
    unittest.main()
