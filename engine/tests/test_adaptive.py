import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.models import Status
from engine.cache import DataUnavailable
from engine.ingest import Ingest
from engine.service import EngineService

V='62.11.01.2002'
OTHER='62.11.01.2003'


def observation(wt,**kwargs):
    return dict(confirmed=True,wt_cm=wt,mode='live',day=0,**kwargs)


class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'isolated'/'engine.json'
        self.engine=EngineService(Ingest(),state_path=self.path)

    def test_accepted_recomputes_before_after_version_and_evidence(self):
        before=self.engine.village_status(V)
        forecast=self.engine.forecast(V)
        result=self.engine.recalibrate(V,observation(before['wt_cm']+20),'o1')
        self.assertTrue(result['recalibrated'])
        self.assertEqual(result['version'],2)
        self.assertEqual(result['old'],before)
        self.assertAlmostEqual(result['pre_update_discrepancy_cm'],20)
        self.assertEqual(result['applied_initial_wt_delta_cm'],15)
        self.assertNotEqual(result['new']['wt_cm'],before['wt_cm'])
        self.assertNotEqual(self.engine.forecast(V),forecast)
        self.assertEqual(result['new']['confidence'],.7)
        self.assertEqual(result['new']['wt_source'],'modelled')
        Status.model_validate(result['new'])
        history=self.engine.params_history(V)
        self.assertEqual(history[-1]['observation_id'],'o1')
        self.assertEqual(history[-1]['old'],before)
        self.assertIn('not K/Sy',history[-1]['params']['provenance'])
        json.dumps(result,allow_nan=False)
        result['old']['wt_cm']=-999
        self.assertEqual(self.engine.params_history(V)[-1]['old'],before)

    def test_simulation_and_history_api_use_updated_village_state(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from engine.api import create_router
        old=self.engine.village_status(V)
        updated=self.engine.recalibrate(V,observation(old['wt_cm']+10),'o1')
        request=dict(type='canal_block',targets=['K4'],delta_hc_cm=60)
        simulated=self.engine.simulate(V,request)
        self.assertEqual(simulated['curve_baseline'][0]['wt_cm'],updated['new']['wt_cm'])
        self.assertEqual(simulated['curve_scenario'][0]['wt_cm'],updated['new']['wt_cm'])
        app=FastAPI();app.include_router(create_router(self.engine),prefix='/api')
        response=TestClient(app).get(f'/api/villages/{V}/params/history')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['items'][-1]['version'],2)
        self.assertEqual(response.json()['items'][-1]['old'],old)

    def test_no_cross_village_or_replay_mutation(self):
        other=self.engine.village_status(OTHER)
        replay=self.engine.forecast(V,mode='replay',day=9)
        old=self.engine.village_status(V)
        self.engine.recalibrate(V,observation(old['wt_cm']+10),'o1')
        self.assertEqual(self.engine.village_status(OTHER),other)
        self.assertEqual(self.engine.forecast(V,mode='replay',day=9),replay)
        self.assertEqual(len(self.engine.params_history(OTHER)),1)
        self.assertEqual(self.engine.village_status(V,'replay',9)['confidence'],.5)

    def test_large_disagreement_held_preserves_snapshot(self):
        old=self.engine.village_status(V)
        result=self.engine.recalibrate(V,observation(old['wt_cm']+31),'hold1')
        self.assertFalse(result['recalibrated'])
        self.assertEqual(result['reason'],'discrepancy_requires_verification')
        self.assertEqual(result['new'],old)
        self.assertEqual(result['version'],1)
        self.assertEqual(len(self.engine.params_history(V)),1)
        self.assertGreater(result['pre_update_discrepancy_cm'],30)

    def test_replay_confirmed_observation_never_persists(self):
        before=self.engine.params_history(V)
        result=self.engine.recalibrate(V,dict(confirmed=True,wt_cm=60,mode='replay',day=9),'replay1')
        self.assertFalse(result['recalibrated'])
        self.assertEqual(result['reason'],'replay_skips_recalibration')
        self.assertEqual(result['old'],result['new'])
        self.assertEqual(self.engine.params_history(V),before)
        self.assertFalse(self.path.exists())

    def test_idempotence_conflicts_and_restart(self):
        old=self.engine.village_status(V)
        data=observation(old['wt_cm']+10)
        first=self.engine.recalibrate(V,data,'o1')
        with patch.object(self.engine,'village_status',side_effect=AssertionError('Duplicate must not fetch')):
            duplicate=self.engine.recalibrate(V,data,'o1')
        self.assertEqual(duplicate,first)
        self.assertEqual(len(self.engine.params_history(V)),2)
        restarted=EngineService(Ingest(),state_path=self.path)
        self.assertEqual(restarted.params_history(V),self.engine.params_history(V))
        self.assertEqual(restarted.village_status(V),self.engine.village_status(V))
        self.assertEqual(restarted.recalibrate(V,data,'o1'),first)
        with self.assertRaises(ValueError):
            restarted.recalibrate(OTHER,data,'o1')
        with self.assertRaises(ValueError):
            restarted.recalibrate(V,observation(old['wt_cm']+11),'o1')

    def test_bounds_invalid_rejected_and_repeated_updates_bounded(self):
        for value in [None,True,-1,151,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):
                self.engine.recalibrate(V,observation(value),'invalid')
        with self.assertRaises(ValueError):
            self.engine.recalibrate(V,dict(confirmed=False,wt_cm=50),'invalid')
        for index in range(15):
            current=self.engine.village_status(V)['wt_cm']
            result=self.engine.recalibrate(V,observation(min(150,current+15)),f'o{index}')
            self.assertLessEqual(abs(result['applied_initial_wt_delta_cm']),15)
            self.assertTrue(0<=self.engine.params_history(V)[-1]['params']['initial_wt_cm']<=150)
        self.assertEqual(len(self.engine.params_history(OTHER)),1)

    def test_persistence_error_rolls_back_and_bad_file_fails_closed(self):
        old=self.engine.village_status(V)
        with patch.object(self.engine,'_persist',side_effect=DataUnavailable('isolated failure')):
            with self.assertRaises(DataUnavailable):
                self.engine.recalibrate(V,observation(old['wt_cm']+10),'o1')
        self.assertEqual(self.engine.village_status(V),old)
        self.assertEqual(len(self.engine.params_history(V)),1)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.path.write_text('{corrupt')
        with self.assertRaises(DataUnavailable):
            EngineService(Ingest(),state_path=self.path)


if __name__=='__main__':
    unittest.main()
