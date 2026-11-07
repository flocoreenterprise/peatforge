import math
import unittest
from dataclasses import replace
from pathlib import Path

import yaml

from engine.groundwater import (GroundwaterParams, NumericalFailure, load_params,
                                solve_groundwater)


class GroundwaterTests(unittest.TestCase):
    def test_flat_table_zero_forcing_stationary(self):
        result = solve_groundwater([0]*10, [0]*10, initial_wt_cm=60, canal_wt_cm=60)
        for point in result['items']:
            self.assertAlmostEqual(point['wt_cm'], 60, places=10)
            self.assertTrue(all(abs(h - 23.4) < 1e-12 for h in point['profile_m']))
            self.assertLess(point['mass_balance']['relative_error'], 0.01)

    def test_rain_raises_table_and_et_lowers_it(self):
        common = dict(initial_wt_cm=60, canal_wt_cm=60)
        dry = solve_groundwater([0]*5, [0]*5, **common)
        wet = solve_groundwater([10]*5, [0]*5, **common)
        et = solve_groundwater([0]*5, [5]*5, **common)
        self.assertLess(wet['items'][-1]['wt_cm'], dry['items'][-1]['wt_cm'])
        self.assertGreater(et['items'][-1]['wt_cm'], dry['items'][-1]['wt_cm'])
        # Early middle-node change approximates 10mm / Sy = 50mm.
        self.assertAlmostEqual(wet['items'][0]['wt_cm'], 55, delta=0.02)

    def test_boundary_raise_benchmark_is_disclosed_assumption(self):
        baseline = solve_groundwater([0]*30, [0]*30, initial_wt_cm=60, canal_wt_cm=60)
        blocked = solve_groundwater([0]*30, [0]*30, initial_wt_cm=60, canal_wt_cm=0)
        gain_m = (baseline['items'][-1]['wt_cm'] - blocked['items'][-1]['wt_cm'])/100
        self.assertAlmostEqual(gain_m, 0.35, delta=0.02)
        self.assertIn('team assumption', ' '.join(blocked['assumptions']))

    def test_daily_and_cumulative_mass_balance_with_variable_boundary(self):
        result = solve_groundwater([4, 0, 20, 0, 2]*6, [4]*30,
                                   canal_wt_cm=[80, 80, 40, 40, 60]*6)
        self.assertLess(result['mass_balance']['relative_error'], 0.01)
        for point in result['items']:
            diag = point['mass_balance']
            self.assertLess(diag['relative_error'], 0.01)
            self.assertLessEqual(diag['max_stability_ratio'], 0.90 + 1e-12)
            self.assertGreater(diag['substeps'], 1)
        self.assertEqual(result['interior_control_length_m'], 392)

    def test_steep_peak_preserves_monotonicity_and_no_new_extrema(self):
        profile = [0.0] * 51
        profile[25] = 24.0
        result = solve_groundwater([0]*5, [0]*5, initial_profile_m=profile,
                                   canal_wt_cm=2400, allow_fallback=False)
        previous_max = 24.0
        for point in result['items']:
            heights = point['profile_m']
            self.assertGreaterEqual(min(heights), 0)
            self.assertLessEqual(max(heights), previous_max + 1e-12)
            self.assertEqual(max(heights), heights[25])
            self.assertTrue(all(a <= b + 1e-12 for a, b in zip(heights[:25], heights[1:26])))
            self.assertTrue(all(a + 1e-12 >= b for a, b in zip(heights[25:-1], heights[26:])))
            self.assertLess(point['mass_balance']['relative_error'], 0.01)
            previous_max = max(heights)

    def test_clipping_accounts_runoff_and_unmet_demand(self):
        wet = solve_groundwater([1000], [0], initial_wt_cm=0, canal_wt_cm=0)
        self.assertGreater(wet['mass_balance']['runoff_m2'], 0)
        self.assertLess(wet['mass_balance']['relative_error'], 0.01)
        dry = solve_groundwater([0], [1000], initial_wt_cm=2400, canal_wt_cm=2400)
        self.assertGreater(dry['mass_balance']['unmet_demand_m2'], 0)
        self.assertLess(dry['mass_balance']['relative_error'], 0.01)
        for result in (wet, dry):
            self.assertTrue(all(0 <= h <= 24 for h in result['items'][0]['profile_m']))

    def test_bucket_fallback_same_schema_and_honest_reason(self):
        result = solve_groundwater([5, 0], [3, 3], max_substeps_per_day=1)
        self.assertEqual(result['method'], 'bucket')
        self.assertIn('substep', result['fallback_reason'])
        self.assertEqual(len(result['items'][0]['profile_m']), 51)
        self.assertLess(result['mass_balance']['relative_error'], 0.01)
        self.assertIsNone(result['items'][0]['mass_balance']['max_stability_ratio'])
        with self.assertRaises(NumericalFailure):
            solve_groundwater([0], [0], max_substeps_per_day=1, allow_fallback=False)

    def test_explicit_bucket_is_labelled_and_stationary(self):
        result = solve_groundwater([0]*3, [0]*3, initial_wt_cm=60,
                                   canal_wt_cm=60, method='bucket')
        self.assertEqual(result['method'], 'bucket')
        self.assertAlmostEqual(result['items'][-1]['wt_cm'], 60)
        self.assertTrue(any('approximation' in a for a in result['assumptions']))

    def test_restart_from_profile_matches_continuous_run(self):
        continuous = solve_groundwater([2]*4, [4]*4)
        first = solve_groundwater([2]*2, [4]*2)
        second = solve_groundwater([2]*2, [4]*2,
                                   initial_profile_m=first['items'][-1]['profile_m'])
        self.assertEqual(continuous['items'][-1]['profile_m'], second['items'][-1]['profile_m'])

    def test_missing_or_nonphysical_input_rejected_no_fallback(self):
        for rain, et in (([None], [0]), ([float('nan')], [0]), ([-1], [0]),
                         ([0], [float('inf')]), ([], []), ([0], [0, 0])):
            with self.assertRaises(ValueError):
                solve_groundwater(rain, et)
        for kwargs in ({'canal_wt_cm': -1}, {'initial_wt_cm': -1},
                       {'initial_profile_m': [1]*50}, {'canal_wt_cm': [40, 50]},
                       {'params': replace(GroundwaterParams(), specific_yield=0)},
                       {'params': replace(GroundwaterParams(), nodes=50)}):
            with self.assertRaises(ValueError):
                solve_groundwater([0], [0], **kwargs)

    def test_yaml_values_have_sources_and_match_defaults(self):
        payload = yaml.safe_load(Path('engine/params.yaml').read_text(encoding='utf-8'))
        self.assertTrue(all(record['source'] for record in payload['groundwater'].values()))
        self.assertEqual(load_params(), GroundwaterParams())


if __name__ == '__main__':
    unittest.main()
