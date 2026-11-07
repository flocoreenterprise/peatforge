import copy
import json
import unittest
from datetime import date, timedelta
from dataclasses import replace
from unittest.mock import patch

from agents.models import ForecastPoint, Simulation, Status
from engine.cache import DataUnavailable
from engine.ingest import Ingest
from engine.service import EngineService, compare_curves, critical_index, replay_meta

V='62.11.01.2002'


def curve(depths):
    return [dict(date=(date(2019,6,15)+timedelta(days=i)).isoformat(),wt_cm=d,pfvi=150,level='SEDANG',rain_mm=0)
            for i,d in enumerate(depths)]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine=EngineService(Ingest())

    def test_crossing_null_zero_positive_lower_bound(self):
        none=compare_curves(curve([40,45,50]),curve([40,43,45]))
        self.assertIsNone(none['days_bought'])
        self.assertEqual(none['reason'],'baseline_not_critical_within_horizon')
        zero=compare_curves(curve([60,65,70]),curve([60,62,64]))
        self.assertEqual(zero['days_bought'],0)
        self.assertEqual(zero['reason'],'baseline_already_critical')
        positive=compare_curves(curve([40,60,65,70]),curve([40,50,55,60]))
        self.assertEqual(positive['days_bought'],2)
        lower=compare_curves(curve([40,60,65,70]),curve([40,45,50,55]))
        self.assertIsNone(lower['days_bought'])
        self.assertEqual(lower['at_least_days'],3)
        self.assertEqual(lower['reason'],'scenario_beyond_horizon')
        self.assertEqual(lower['wt_gain_cm'],15)
        Simulation.model_validate(lower)

    def test_critical_either_index_or_depth(self):
        points=curve([40,45])
        points[1]['pfvi']=250
        self.assertEqual(critical_index(points),1)
        points[1]['pfvi']=None
        points[1]['wt_cm']=60
        self.assertEqual(critical_index(points),1)

    def test_current_critical_separate_from_pfvi_level_and_future_crossing(self):
        run = self.engine._run(V, 'replay', 0)
        for wt, pfvi, label, reason in [(60, 50, 'RENDAH', 'wt'),
                                         (59.99, 250, 'EKSTREM', 'pfvi'),
                                         (60, 250, 'EKSTREM', 'both'),
                                         (59.99, 249.99, 'TINGGI', None)]:
            with self.subTest(wt=wt, pfvi=pfvi):
                synthetic = dict(run, curve=[dict(date='2019-06-15', wt_cm=wt, pfvi=pfvi, level=label),
                                             dict(date='2019-06-16', wt_cm=60, pfvi=250, level='EKSTREM')])
                with patch.object(self.engine, '_run', return_value=synthetic):
                    status = self.engine.village_status(V, 'replay', 0)
                    Status.model_validate(status)
                    self.assertEqual(status['level'], label)
                    self.assertEqual(status['pfvi_level'], label)
                    self.assertEqual(status['critical_now'], reason is not None)
                    self.assertEqual(status['critical_reason'], reason)
                    self.assertEqual(status['days_to_critical'], 0 if reason else 1)
                    self.assertEqual(status['critical_date'], '2019-06-15' if reason else '2019-06-16')
                    props = self.engine.map_risk('replay', 0)['features'][0]['properties']
                    self.assertEqual(props['critical_now'], status['critical_now'])
                    self.assertEqual(props['critical_reason'], reason)

    def test_fixture_status_forecast_simulation_strict_agent_shapes(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('network prohibited')):
            status=self.engine.village_status(V,'replay',12)
            Status.model_validate(status)
            self.assertEqual(status['source'],'fixture')
            for point in self.engine.forecast(V,mode='replay',day=61):
                ForecastPoint.model_validate(point)
            result=self.engine.simulate(V,dict(type='canal_block',targets=['K4'],delta_hc_cm=60,days=14,mode='replay',day=12))
            Simulation.model_validate(result)
            self.assertEqual(len(result['curve_baseline']),15)
            self.assertEqual(result['curve_baseline'][0],result['curve_scenario'][0])
            self.assertGreater(result['wt_gain_cm'],0)
            json.dumps([status,result],allow_nan=False)

    def test_no_action_no_invented_hydraulic_benefit(self):
        result=self.engine.simulate(V,dict(type='pump_standby',targets=['K4'],mode='replay',day=5))
        self.assertAlmostEqual(result['wt_gain_cm'],0,places=9)
        self.assertAlmostEqual(result['pfvi_reduction'],0,places=9)
        self.assertTrue(any('No quantitative' in a for a in result['assumptions']))

    def test_simulation_reports_actual_setup_and_single_strip_target_semantics(self):
        self.engine.hydraulic = replace(self.engine.hydraulic, spacing_m=350,
                                        conductivity_m_day=4, specific_yield=.25,
                                        effective_thickness_m=20)
        request = dict(type='canal_block', targets=['K4'], mode='replay', day=5, days=14)
        single = self.engine.simulate(V, request)
        multiple = self.engine.simulate(V, dict(request, targets=['K4', 'K5']))
        self.assertEqual(single, multiple)
        self.assertEqual(single['model_setup'], {
            'spacing_m':350, 'nodes':51, 'conductivity_m_day':4, 'specific_yield':.25,
            'effective_hydraulic_thickness_m':20, 'boundary_application':'both_endpoints',
            'target_effect':'single_symmetric_strip', 'horizon_days':14,
            'provenance':'team_assumption_not_field_calibrated'})
        self.assertEqual(len(single['curve_baseline']), 15)
        self.assertEqual(len(single['curve_scenario']), 15)
        Simulation.model_validate(single)
        with self.assertRaises(ValueError):
            self.engine.simulate(V, dict(request, days=15))

    def test_replay_calendar_and_metadata_point_map(self):
        self.assertEqual(replay_meta()['days'],62)
        first,last=self.engine.replay(0),self.engine.replay(61)
        self.assertEqual((first['date'],last['date']),('2019-06-15','2019-08-15'))
        self.assertEqual(len(last['items']),5)
        geo=self.engine.map_risk('replay',5)
        self.assertEqual(geo['features'][0]['geometry']['type'],'Point')
        self.assertTrue(any('perfect-forecast' in a for a in first['items'][0]['assumptions']))

    def test_default_hotspots_union_pilot_circles_deduplicated(self):
        class Regional(Ingest):
            def hotspots(self):
                local=dict(id='local',lat=-3.1222040246,lon=114.0981588862,
                           detected_at='2019-06-15T12:00:00Z',source='fixture')
                distant=dict(local,id='distant',lat=0,lon=120)
                return dict(items=[local,local.copy(),distant],available=True,stale=True,
                            data_as_of='2019-06-15T12:00:00Z',source='fixture',partial=True)
        service=EngineService(Regional())
        response=service.get_hotspots(radius_km=5)
        self.assertEqual([h['id'] for h in response['items']],['local'])
        self.assertTrue(response['available'])
        self.assertTrue(response['stale'])
        self.assertTrue(response['partial'])
        self.assertEqual(response['data_as_of'],'2019-06-15T12:00:00Z')
        self.assertEqual(service.get_hotspots('62.11.01.2004',radius_km=1)['items'],[])

    def test_hotspot_replay_unavailable_and_no_network(self):
        self.assertFalse(self.engine.get_hotspots(V,mode='replay',day=5)['available'])
        self.assertFalse(self.engine.backtest(V)['available'])
        self.assertEqual(len(self.engine.params_history(V)),1)
        self.assertEqual(self.engine.get_canals(V)[3]['source'],'fixture')

    def test_parameter_initial_timestamp_is_actual_configuration_not_replay(self):
        engine=EngineService(Ingest(), clock=lambda: 1794016800)
        history=engine.params_history(V)
        self.assertTrue(history[0]['created_at'].startswith('2026-'))
        self.assertNotEqual(history[0]['created_at'],'2019-06-15T00:00:00Z')
        self.assertIn('team-assumption',history[0]['params']['provenance'])
        self.assertEqual(history,engine.params_history(V))

    def test_weather_missing_is_unavailable_not_zero(self):
        class Missing(Ingest):
            def weather(self,*args,**kwargs):
                data=super().weather(*args,**kwargs)
                data['items'][0]['rain_mm']=None
                return data
        with self.assertRaises(DataUnavailable):
            EngineService(Missing()).village_status(V)

    def test_future_soil_missing_not_used_as_fabricated_calibration(self):
        class Missing(Ingest):
            def weather(self,*args,**kwargs):
                data=super().weather(*args,**kwargs)
                data['items'][-1]['soil_moisture_pct']=None
                return data
        Status.model_validate(EngineService(Missing()).village_status(V))

    def test_invalid_context_and_inputs_rejected(self):
        for mode,day in [('bad',0),('replay',62),('replay',-1),('live',1)]:
            with self.assertRaises(ValueError):
                self.engine.village_status(V,mode,day)
        for request in [dict(type='canal_block',targets=['K999']),dict(type='fake',targets=['K4']),
                        dict(type='canal_block',targets=['K4'],days=90),
                        dict(type='canal_block',targets=['K4'],delta_hc_cm=float('nan'))]:
            with self.assertRaises(ValueError):
                self.engine.simulate(V,request)


if __name__=='__main__':
    unittest.main()
