"""Engine application interface: real cached environmental inputs by default.

No network runs at import. Tests must instantiate EngineService(Ingest()).
Public environmental providers are independent of communications/auth settings.
"""
from __future__ import annotations

import copy
import hashlib
import math
import json
import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .cache import DataUnavailable, DiskCache
from .groundwater import load_params, solve_groundwater
from .ingest import Ingest
from .pfvi import ASSUMPTIONS, diobs, level, load_pfvi_config, run_pfvi
from .villages import canals, get_village, list_villages

START = date(2019, 6, 15)
END = date(2019, 8, 15)
REPLAY_DAYS = (END - START).days + 1
UTC = timezone.utc


def replay_meta() -> dict:
    return {'scenario': '2019', 'start': START.isoformat(), 'end': END.isoformat(),
            'days': REPLAY_DAYS, 'perfect_forecast': True}


def critical_index(curve: list[dict]) -> int | None:
    return next((i for i, point in enumerate(curve)
                 if ((point['pfvi'] is not None and point['pfvi'] >= 250) or
                     (point['wt_cm'] is not None and point['wt_cm'] >= 60))), None)


def critical_reason(point: dict) -> str | None:
    """Current model-state criticality is separate from the PFVI-only level."""
    wt = point['wt_cm'] is not None and point['wt_cm'] >= 60
    pfvi = point['pfvi'] is not None and point['pfvi'] >= 250
    return 'both' if wt and pfvi else 'wt' if wt else 'pfvi' if pfvi else None


def compare_curves(baseline: list[dict], scenario: list[dict], assumptions: list[str] | None = None) -> dict:
    """Discrete crossing semantics; curve index zero is the current state."""
    if not baseline or len(baseline) != len(scenario):
        raise ValueError('Matching nonempty curves required')
    b, s = critical_index(baseline), critical_index(scenario)
    days, at_least, reason = None, None, None
    if b == 0:
        days, reason = 0, 'baseline_already_critical'
    elif b is None:
        reason = 'baseline_not_critical_within_horizon'
    elif s is None:
        # First possible unobserved crossing is the day AFTER the final point.
        at_least, reason = len(scenario) - b, 'scenario_beyond_horizon'
    else:
        days = max(0, s - b)
        if s <= b:
            reason = 'scenario_not_delayed'
    notes = list(assumptions or [])
    if b == 0:
        notes.append('Already critical: compare horizon-end groundwater/PFVI benefit; no additional noncritical days claimed')
    return {'baseline_critical_date': baseline[b]['date'] if b is not None else None,
            'scenario_critical_date': scenario[s]['date'] if s is not None else None,
            'days_bought': days, 'at_least_days': at_least, 'reason': reason,
            'wt_gain_cm': baseline[-1]['wt_cm'] - scenario[-1]['wt_cm'],
            'pfvi_reduction': baseline[-1]['pfvi'] - scenario[-1]['pfvi'],
            'curve_baseline': baseline, 'curve_scenario': scenario, 'assumptions': notes}


def _distance_km(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    x = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(lon2-lon1)/2)**2
    return 6371 * 2 * math.asin(min(1.0, math.sqrt(x)))


class EngineService:
    def __init__(self, ingest: Ingest | None = None, *, clock=time.time, state_path: str | Path | None = None):
        self.ingest = ingest or Ingest(DiskCache(Path(__file__).resolve().parent.parent / 'runtime/engine/http'), mode='live')
        self.clock = clock
        self.initial_configuration_at = datetime.fromtimestamp(self.clock(), UTC).isoformat().replace('+00:00','Z')
        self.hydraulic = load_params()
        self.pfvi_config = load_pfvi_config()
        self._runs = {}
        self.state_path = Path(state_path) if state_path is not None else None
        self._state_lock = threading.RLock()
        self._village_locks = {v['id']:threading.RLock() for v in list_villages()}
        self._state = {'schema':1,'villages':{},'observations':{}}
        if self.state_path is not None and self.state_path.exists():
            try:
                loaded = json.loads(self.state_path.read_text(encoding='utf-8'))
                if loaded['schema'] != 1 or not isinstance(loaded['observations'],dict):
                    raise ValueError('Invalid state schema')
                for village_id,record in loaded['villages'].items():
                    get_village(village_id)
                    value=record['initial_wt_cm']
                    if not math.isfinite(value) or not 0 <= value <= 150 or not isinstance(record['version'],int) or record['version'] < 1:
                        raise ValueError('Invalid state bounds')
                    if not record['history'] or record['history'][-1]['version'] != record['version']:
                        raise ValueError('Invalid parameter history')
                json.dumps(loaded,allow_nan=False)
                self._state=loaded
            except (OSError,ValueError,TypeError,KeyError):
                raise DataUnavailable('Adaptive state unavailable or invalid') from None
        for village in list_villages():
            self._ensure_village_state(village['id'])

    def _ensure_village_state(self,village_id):
        if village_id not in self._state['villages']:
            self._state['villages'][village_id] = {'initial_wt_cm':40.0,'version':1,'confirmed':False,
                'history':[{'version':1,'params':{'groundwater':self.hydraulic.__dict__,
                    'pfvi':self.pfvi_config['params'].__dict__,'initial_wt_cm':40.0,
                    'provenance':'Initial team-assumption configuration; not field calibration'},
                    'observation_id':None,'created_at':self.initial_configuration_at}]}

    def _persist(self):
        if self.state_path is None:
            return
        temp=None
        try:
            self.state_path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=self.state_path.parent,delete=False) as handle:
                temp=handle.name
                json.dump(self._state,handle,allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp,self.state_path)
        except OSError:
            raise DataUnavailable('Adaptive state could not be saved') from None
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)

    def _run(self,village_id,mode,day):
        get_village(village_id)
        with self._village_locks[village_id]:
            return self._compute_run(village_id,mode,day)

    def _compute_run(self, village_id, mode, day):
        get_village(village_id)
        if mode not in ('live', 'replay') or not isinstance(day, int) or not 0 <= day < REPLAY_DAYS:
            raise ValueError('Invalid mode/day')
        if mode == 'live' and day != 0:
            raise ValueError('Live mode requires day=0')
        state = self._state['villages'][village_id]
        version = state['version'] if mode == 'live' else 1
        key = (village_id, mode, version)
        entry = self._runs.get(key)
        if entry and self.clock() - entry[0] < (300 if mode == 'live' else 86400):
            return entry[1]
        weather = (self.ingest.weather(village_id, start=START, end=END+timedelta(days=14))
                   if mode == 'replay' else self.ingest.weather(village_id, days=16))
        rows = weather['items']
        for row in rows:
            if any(row[k] is None for k in ('rain_mm','et0_mm','temperature_c')):
                raise DataUnavailable('Required weather or mineral-soil proxy unavailable')
        if not rows or rows[0]['soil_moisture_pct'] is None:
            raise DataUnavailable('Initial mineral-soil moisture proxy unavailable')
        gw = solve_groundwater([r['rain_mm'] for r in rows], [r['et0_mm'] for r in rows],
                               params=self.hydraulic, initial_wt_cm=state['initial_wt_cm'] if mode=='live' else 40, canal_wt_cm=80)
        config = self.pfvi_config
        initial = diobs(rows[0]['soil_moisture_pct'], config['fc'], config['sat'])
        pfvi = run_pfvi([r['wt_cm'] for r in gw['items']], [r['rain_mm'] for r in rows],
                        [r['temperature_c'] for r in rows], params=config['params'], initial_pfvi=initial,
                        annual_rain_mm=config['annual_rain_mm'])
        curve = [dict(date=r['date'], wt_cm=g['wt_cm'], pfvi=p['pfvi'], level=p['level'], rain_mm=r['rain_mm'])
                 for r,g,p in zip(rows,gw['items'],pfvi['items'])]
        assumptions = weather['assumptions'] + gw['assumptions'] + ASSUMPTIONS + [
            f"Initial groundwater depth {state['initial_wt_cm'] if mode=='live' else 40}cm; canal depth 80cm is a team assumption",
            'PFVI starts from first-day mineral-soil DIobs; no independent field calibration',
            'Daily states represent modelled end-of-day forcing, including forecast rain; not current field measurements',
            'BMKG village coordinates are reference points, not official boundaries',
            'Regional gridded weather, not village sensor measurements; spatial precision is limited',
            f"Input source={weather['source']}; cached={weather.get('cached', False)}; stale={weather['stale']}",
        ]
        if mode == 'replay':
            assumptions.append('ERA5 historical perfect-forecast replay, not an out-of-sample prediction')
        accepted = mode=='live' and state.get('confirmed',False)
        if accepted:
            assumptions.append('Bounded initial-condition assimilation after one confirmed observation; K/Sy not calibrated; confidence0.7 is heuristic, not validated probability')
        result = dict(weather=weather, groundwater=gw, curve=curve, assumptions=assumptions,
                      confidence=.7 if accepted else .5)
        # A browser may reuse simulations only while every numerical run input
        # agrees. Provider timestamps and request time are not numerical inputs.
        simulation_inputs = {
            'schema': 'peatforge-simulation-input-v1', 'village_id': village_id, 'mode': mode,
            'weather': rows, 'groundwater_params': self.hydraulic.__dict__,
            'pfvi_config': dict(config, params=config['params'].__dict__),
            'initial_wt_cm': state['initial_wt_cm'] if mode == 'live' else 40,
            'state_version': version, 'canal_wt_cm': 80,
        }
        result['simulation_input_version'] = hashlib.sha256(json.dumps(
            simulation_inputs, sort_keys=True, separators=(',', ':'), allow_nan=False
        ).encode('utf-8')).hexdigest()
        self._runs[key] = (self.clock(), result)
        return result

    def village_status(self, village_id: str, mode: str = 'live', day: int = 0) -> dict:
        run = self._run(village_id, mode, day)
        curve = run['curve'][day:day+15]
        point, crossing = curve[0], critical_index(curve)
        current_reason = critical_reason(point)
        weather = run['weather']
        return dict(village_id=village_id, level=point['level'], pfvi=point['pfvi'], wt_cm=point['wt_cm'],
                    pfvi_level=point['level'], critical_now=current_reason is not None, critical_reason=current_reason,
                    simulation_input_version=run['simulation_input_version'],
                    days_to_critical=crossing, critical_date=curve[crossing]['date'] if crossing is not None else None,
                    confidence=run['confidence'], data_as_of=weather['data_as_of'], wt_source='modelled',
                    source='fixture' if weather['source']=='fixture' else 'modelled', stale=weather['stale'],
                    mode=mode, day=day, assumptions=run['assumptions'],
                    reason=None if crossing is not None else 'No critical crossing within 14-day horizon')

    def forecast(self, village_id: str, days: int = 14, mode: str = 'live', day: int = 0) -> list[dict]:
        if not isinstance(days, int) or not 1 <= days <= 14:
            raise ValueError('Forecast horizon must be 1..14 days')
        return copy.deepcopy(self._run(village_id,mode,day)['curve'][day:day+days])

    def simulate(self, village_id: str, request: dict) -> dict:
        action = request.get('type')
        if action not in ('canal_block','pump_standby','dispatch_verify'):
            raise ValueError('Unknown action')
        targets = request.get('targets', [])
        if not targets or any(t not in {c['id'] for c in canals(village_id)} for t in targets):
            raise ValueError('Simulation requires known canal targets')
        days, day, mode = request.get('days',14), request.get('day',0), request.get('mode','live')
        if not isinstance(days,int) or not 1 <= days <= 14:
            raise ValueError('Simulation horizon must be 1..14 days')
        delta = request.get('delta_hc_cm')
        delta = 40.0 if delta is None else float(delta)
        if not math.isfinite(delta) or not 0 <= delta <= 150:
            raise ValueError('Canal boundary raise must be 0..150cm')
        run = self._run(village_id,mode,day)
        baseline = copy.deepcopy(run['curve'][day:day+days+1])
        forcing = run['weather']['items'][day+1:day+days+1]
        if len(forcing) != days:
            raise DataUnavailable('Weather horizon insufficient for simulation')
        hydraulic_effect = delta if action == 'canal_block' else 0
        gw = solve_groundwater([r['rain_mm'] for r in forcing], [r['et0_mm'] for r in forcing],
                               params=self.hydraulic, initial_profile_m=run['groundwater']['items'][day]['profile_m'],
                               canal_wt_cm=max(0,80-hydraulic_effect))
        cfg = self.pfvi_config
        pfvi = run_pfvi([p['wt_cm'] for p in gw['items']], [r['rain_mm'] for r in forcing],
                        [r['temperature_c'] for r in forcing], params=cfg['params'],
                        initial_pfvi=baseline[0]['pfvi'], previous_rain_mm=baseline[0]['rain_mm'],
                        annual_rain_mm=cfg['annual_rain_mm'])
        scenario = [baseline[0].copy()] + [dict(date=r['date'], wt_cm=g['wt_cm'], pfvi=p['pfvi'], level=p['level'],rain_mm=r['rain_mm'])
                                          for r,g,p in zip(forcing,gw['items'],pfvi['items'])]
        assumptions = run['assumptions'] + [
            'Canals/resources are fixture layouts; selected blocks represent one symmetric strip boundary change, not cumulative effects',
            f'Boundary raise capped by peat surface: applied {min(80,hydraulic_effect)}cm',
        ]
        if action != 'canal_block':
            assumptions.append('No quantitative hydraulic effect is assigned to standby or verification; benefit is operational, not modelled days')
        result = compare_curves(baseline,scenario,assumptions)
        result['model_setup'] = {
            'spacing_m': self.hydraulic.spacing_m, 'nodes': self.hydraulic.nodes,
            'conductivity_m_day': self.hydraulic.conductivity_m_day,
            'specific_yield': self.hydraulic.specific_yield,
            'effective_hydraulic_thickness_m': self.hydraulic.effective_thickness_m,
            'boundary_application': 'both_endpoints', 'target_effect': 'single_symmetric_strip',
            'horizon_days': days, 'provenance': 'team_assumption_not_field_calibrated',
        }
        return result

    def get_canals(self, village_id: str) -> list[dict]:
        return canals(village_id)

    def get_resources(self, village_id: str) -> dict:
        return {'pumps':1,'crews':1,'canal_blocks':[{'id':c['id'],'status':c['block_status']} for c in canals(village_id)]}

    def get_hotspots(self, village_id: str | None = None, hours: int = 48, radius_km: float = 5,
                     mode: str = 'live', day: int = 0) -> dict:
        if not 1 <= hours <= 168 or not 0 < radius_km <= 100 or mode not in ('live','replay') or not 0 <= day < REPLAY_DAYS:
            raise ValueError('Invalid hotspot query')
        if mode == 'live' and day != 0:
            raise ValueError('Live mode requires day=0')
        scope = [get_village(village_id)] if village_id else list_villages()
        if mode == 'replay':
            return {'items':[], 'available':False,'stale':False,'data_as_of':None,
                    'reason':'Historical hotspots unavailable without configured FIRMS archive key; live detections are not replayed'}
        data = self.ingest.hotspots()
        now = datetime.fromtimestamp(self.clock(),UTC) if self.ingest.mode == 'live' else datetime(2019,6,16,tzinfo=UTC)
        items = [h for h in data['items'] if 0 <= (now-datetime.fromisoformat(h['detected_at'].replace('Z','+00:00'))).total_seconds() <= hours*3600
                 and any(_distance_km(v['lat'],v['lon'],h['lat'],h['lon']) <= radius_km for v in scope)]
        items = list({h['id']:h for h in items}.values())
        return dict(data, items=items)

    def map_risk(self, mode: str = 'live', day: int = 0) -> dict:
        return {'type':'FeatureCollection','features':[{'type':'Feature', 'geometry':{'type':'Point','coordinates':[v['lon'],v['lat']]},
                  'properties':dict(self.village_status(v['id'],mode,day),name=v['name'])} for v in list_villages()]}

    def replay(self, day: int, scenario: str = '2019') -> dict:
        if scenario != '2019':
            raise ValueError('Unknown replay scenario')
        return {'mode':'replay','day':day,'date':(START+timedelta(days=day)).isoformat(),
                'items':[self.village_status(v['id'],'replay',day) for v in list_villages()]}

    def replay_milestones(self, village_id: str) -> dict:
        """First actual model crossings inside the inclusive replay calendar."""
        run = self._run(village_id, 'replay', 0)
        curve = run['curve'][:REPLAY_DAYS]
        thresholds = (
            ('wt_60', lambda p: p['wt_cm'] is not None and p['wt_cm'] >= 60),
            ('pfvi_175', lambda p: p['pfvi'] is not None and p['pfvi'] >= 175),
            ('pfvi_250', lambda p: p['pfvi'] is not None and p['pfvi'] >= 250),
            ('critical', lambda p: critical_reason(p) is not None),
        )
        items = []
        for kind, reached in thresholds:
            day = next((i for i, point in enumerate(curve) if reached(point)), None)
            point = curve[day] if day is not None else None
            items.append({'kind':kind, 'day':day, 'date':point['date'] if point else None,
                          'wt_cm':point['wt_cm'] if point else None, 'pfvi':point['pfvi'] if point else None,
                          'reason':None if point else 'not_reached_in_replay'})
        weather = run['weather']
        return dict(replay_meta(), village_id=village_id, mode='replay', items=items,
                    source='fixture' if weather['source'] == 'fixture' else 'modelled',
                    stale=weather['stale'], data_as_of=weather['data_as_of'],
                    assumptions=run['assumptions'])

    def params_history(self, village_id: str) -> list[dict]:
        get_village(village_id)
        with self._state_lock:
            return copy.deepcopy(self._state['villages'][village_id]['history'])

    def recalibrate(self, village_id: str, observation: dict, observation_id: str) -> dict:
        """Bounded WT initial-condition assimilation; never fit K/Sy from one point.

        Caller must supply confirmed=True and a persistent unique observation_id.
        A pre-update discrepancy >30cm is held without changing parameters.
        Replay does not mutate adaptive state, history, confidence or idempotency.
        """
        get_village(village_id)
        if not isinstance(observation,dict) or observation.get('confirmed') is not True:
            raise ValueError('Confirmed observation required')
        if not isinstance(observation_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',observation_id):
            raise ValueError('Invalid observation identifier')
        if observation.get('village_id',village_id) != village_id:
            raise ValueError('Observation village mismatch')
        try:
            measured=float(observation.get('wt_cm'))
        except (ValueError,TypeError):
            raise ValueError('Plausible finite WT observation required') from None
        if isinstance(observation.get('wt_cm'),bool) or not math.isfinite(measured) or not 0 <= measured <= 150:
            raise ValueError('Plausible finite WT observation required')
        mode,day=observation.get('mode','live'),observation.get('day',0)
        if mode not in ('live','replay') or not isinstance(day,int) or not 0 <= day < REPLAY_DAYS or (mode=='live' and day != 0):
            raise ValueError('Invalid mode/day')
        with self._state_lock, self._village_locks[village_id]:
            record=self._state['villages'][village_id]
            duplicate=self._state['observations'].get(observation_id) if mode=='live' else None
            if duplicate:
                if duplicate['village_id'] != village_id or duplicate['observed_wt_cm'] != measured:
                    raise ValueError('Observation identifier conflict')
                return copy.deepcopy(duplicate['result'])
            old=copy.deepcopy(self.village_status(village_id,mode,day))
            residual=measured-old['wt_cm']
            result=dict(old=old,new=copy.deepcopy(old),version=record['version'],recalibrated=False,
                        reason=None,observation_id=observation_id,pre_update_discrepancy_cm=residual,
                        observed_wt_cm=measured,applied_initial_wt_delta_cm=0.0)
            if mode=='replay':
                result['reason']='replay_skips_recalibration'
                return result
            prior_state=copy.deepcopy(self._state)
            prior_runs=self._runs.copy()
            try:
                if abs(residual)>30:
                    result['reason']='discrepancy_requires_verification'
                else:
                    adjustment=max(-15.0,min(15.0,residual))
                    updated=max(0.0,min(150.0,record['initial_wt_cm']+adjustment))
                    applied=updated-record['initial_wt_cm']
                    record['initial_wt_cm']=updated
                    record['version']+=1
                    record['confirmed']=True
                    new=copy.deepcopy(self.village_status(village_id,'live',0))
                    result.update(new=new,version=record['version'],recalibrated=True,
                                  reason='bounded_initial_condition_assimilation',applied_initial_wt_delta_cm=applied)
                    record['history'].append({'version':record['version'],
                        'params':{'groundwater':self.hydraulic.__dict__,'pfvi':self.pfvi_config['params'].__dict__,
                                  'initial_wt_cm':updated,'provenance':'Bounded initial-condition WT assimilation; not K/Sy calibration'},
                        'observation_id':observation_id,
                        'created_at':datetime.fromtimestamp(self.clock(),UTC).isoformat().replace('+00:00','Z'),
                        'old':old,'new':new,'pre_update_discrepancy_cm':residual,
                        'observed_wt_cm':measured,'applied_initial_wt_delta_cm':applied})
                self._state['observations'][observation_id]={'village_id':village_id,'observed_wt_cm':measured,
                                                           'result':copy.deepcopy(result)}
                self._persist()
            except Exception:
                self._state=prior_state
                self._runs=prior_runs
                raise
            return copy.deepcopy(result)

    def backtest(self, village_id: str) -> dict:
        get_village(village_id)
        return {'available':False,'reason':'Historical FIRMS key/backtest not configured; no validation claimed','hits':None,'misses':None}


_default = None


def _service():
    global _default
    if _default is None:
        _default = EngineService()
    return _default


def village_status(village_id: str, mode: str='live', day: int=0):
    return _service().village_status(village_id,mode,day)


def forecast(village_id: str, days: int=14, mode: str='live', day: int=0):
    return _service().forecast(village_id,days,mode,day)


def simulate(village_id: str, request: dict):
    return _service().simulate(village_id,request)


def get_canals(village_id: str):
    return _service().get_canals(village_id)


def get_resources(village_id: str):
    return _service().get_resources(village_id)


def get_hotspots(village_id: str | None=None, hours: int=48, radius_km: float=5, mode: str='live', day: int=0):
    return _service().get_hotspots(village_id,hours,radius_km,mode,day)


def map_risk(mode: str='live', day: int=0):
    return _service().map_risk(mode,day)


def replay(day: int, scenario: str='2019'):
    return _service().replay(day,scenario)


def params_history(village_id: str):
    return _service().params_history(village_id)


def backtest(village_id: str):
    return _service().backtest(village_id)


def recalibrate(village_id: str, observation: dict, observation_id: str):
    return _service().recalibrate(village_id,observation,observation_id)
