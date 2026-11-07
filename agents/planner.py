"""Deterministic verifier and approval-only planner; no field execution or adaptation."""
from datetime import datetime, timedelta, timezone
import json
from threading import RLock
from typing import Callable
from fastapi import HTTPException
from engine.cache import DataUnavailable
from .channels import ChannelAdapter, ChannelUnavailable
from .db import Database, identifier
from .models import SimulationRequest
from .tools import ToolService
from .events import record


def verify_benefit(proposal: dict, result: dict) -> bool:
    return all(proposal.get(key) == result.get(key) for key in
               ('days_bought', 'at_least_days', 'reason', 'wt_gain_cm', 'pfvi_reduction'))


def has_benefit(result: dict) -> bool:
    return any(result.get(key) is not None and result[key] > 0
               for key in ('days_bought', 'at_least_days', 'wt_gain_cm', 'pfvi_reduction'))


def followup_text(action: dict) -> str:
    prefix = 'Usulan disetujui; perkiraan manfaat jika dilaksanakan: '
    if action['kind'] == 'verify':
        return 'Usulan pemeriksaan disetujui. Petugas perlu memeriksa laporan; pelaksanaan belum dikonfirmasi.'
    if action.get('days_bought') is not None and action['days_bought'] > 0:
        benefit = f"penundaan ambang kritis model {action['days_bought']} hari."
    elif action.get('at_least_days') is not None:
        benefit = f"penundaan ambang kritis model setidaknya {action['at_least_days']} hari; skenario belum melintasi ambang dalam horizon."
    else:
        gain = action['benefit'].get('wt_gain_cm')
        reduction = action['benefit'].get('pfvi_reduction')
        parts = []
        if gain is not None:
            parts.append(f'air tanah lebih dangkal {gain:g} cm')
        if reduction is not None:
            parts.append(f'indeks turun {reduction:g}')
        benefit = ', '.join(parts) + '; tambahan hari tidak diklaim.'
    return prefix + benefit + ' Pelaksanaan belum dikonfirmasi.'


class Planner:
    def __init__(self, database: Database, tools: ToolService, channels: ChannelAdapter,
                 *, clock: Callable[[], datetime] | None = None):
        self.db, self.tools, self.channels = database, tools, channels
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()

    def _now(self) -> str:
        return self.clock().isoformat().replace('+00:00', 'Z')

    def expire(self, connection) -> None:
        connection.execute("UPDATE actions SET status='expired' WHERE status='proposed' AND expires_at<=?", (self._now(),))

    def _action(self, row) -> dict:
        action = json.loads(row['data'])
        action.update(status=row['status'], approved_by=row['approved_by'])
        return action

    def process(self, observation: dict, saved: dict) -> list[str]:
        with self._lock:
            receipt = 'planner:' + saved['observation_id']
            existing = self.db.get(receipt)
            if existing is not None:
                return existing
            old = saved['old']
            checks = [{'name': 'confirmed', 'passed': observation['confirmed']}]
            difference = None if observation.get('wt_cm') is None or old.get('wt_cm') is None else abs(observation['wt_cm'] - old['wt_cm'])
            qualitative=observation.get('wt_cm') is None
            unknown_place=bool(observation.get('place_text') and not observation.get('landmark_id'))
            hold = difference is not None and difference > 30 or qualitative or unknown_place or observation.get('fire') is True
            checks.append({'name': 'water_table_discrepancy', 'passed': None if difference is None else difference<=30,
                           'difference_cm': difference, 'limit_cm': 30, 'model_wt_cm': old.get('wt_cm'),
                           'observed_wt_cm': observation.get('wt_cm')})
            if qualitative or unknown_place:
                hotspots={'available':False,'items':[],'reason':'Human verification required; no numeric inference from categories'}
            else:
                try:
                    hotspots = self.tools.get_hotspots(observation['village_id'], hours=48, radius_km=2,
                                                       mode=observation['mode'], day=observation['day'])
                except DataUnavailable:
                    hotspots = {'available': False, 'items': [], 'reason': 'Hotspot data unavailable'}
            available = hotspots.get('available', False)
            checks.append({'name': 'hotspots_2km_48h', 'passed': None if not available else bool(hotspots['items']),
                           'available': available, 'detections': len(hotspots.get('items', [])),
                           'reason': hotspots.get('reason'), 'note': 'Heat detections are not confirmed fires',
                           'mode': observation['mode'], 'day': observation['day']})
            decision = 'hold' if hold else ('forward_with_flag' if not available or not hotspots.get('items') else 'forward')
            if qualitative or unknown_place:
                checks.append({'name':'reported_observation','passed':None,'confidence_label':'LOW','qualitative':qualitative,'place_unmapped':unknown_place})
            verifier = {'decision': decision, 'checks': checks,'confidence_label':'LOW' if qualitative or unknown_place else 'REPORTED'}
            candidates = []
            if hold:
                title='Perlu dicek: pengamatan warga' if qualitative or unknown_place else 'Perlu dicek: laporan berbeda dari model'
                action=self._base(observation,saved,'dispatch_verify','verify',title,verifier)
                action.update(place_text=observation.get('place_text'),reported_words=observation.get('reported_words',{}),
                              water_category=observation.get('water_category'),smoke_category=observation.get('smoke_category'))
                if observation.get('fire') is True:
                    action.update(priority='URGENT',reason='Warga melaporkan melihat api; perlu pemeriksaan manusia, belum terverifikasi.')
                candidates.append(action)
            else:
                canals = self.tools.get_canals(observation['village_id'])
                resources = self.tools.get_resources(observation['village_id'])
                target = observation.get('landmark_id')
                if target not in {canal['id'] for canal in canals}:
                    target = canals[0]['id'] if canals else None
                for kind in ('canal_block', 'pump_standby'):
                    resource = len([block for block in resources.get('canal_blocks', []) if block.get('status') == 'open']) if kind == 'canal_block' else resources.get('pumps', 0)
                    if not target or resource <= 0:
                        continue
                    request = SimulationRequest(type=kind, targets=[target], delta_hc_cm=60,
                                                mode=observation['mode'], day=observation['day'])
                    result = self.tools.simulate_intervention(observation['village_id'], request).model_dump()
                    if not has_benefit(result):
                        continue
                    action = self._base(observation, saved, kind, 'proposal',
                                        'Usulan sekat kanal' if kind == 'canal_block' else 'Usulan siaga pompa', verifier)
                    action.update({key: result[key] for key in ('days_bought', 'at_least_days')})
                    action['simulation_reason'] = result['reason']
                    action['benefit'] = {key: result[key] for key in ('wt_gain_cm', 'pfvi_reduction')}
                    # Verify nullable and numeric values exactly against deterministic tool output.
                    comparison = {**action, **action['benefit'], 'reason': action['simulation_reason']}
                    if not verify_benefit(comparison, result):
                        raise ValueError('Proposal differs from tool result')
                    action['verifier'] = {'decision': decision, 'checks': checks + [{'name': 'exact_tool_benefit', 'passed': True}]}
                    action['simulation'] = result
                    action['simulation_request'] = request.model_dump()
                    action['assumptions'] = result['assumptions'] + ['Boundary raise 60cm is a chosen scenario, not a recommended field instruction']
                    action['_rank'] = (result['days_bought'] or result['at_least_days'] or 0) / max(1, resource)
                    candidates.append(action)
                candidates.sort(key=lambda action: (action.get('_rank', 0), action['benefit'].get('pfvi_reduction') or 0), reverse=True)
            candidates = candidates[:2]
            ids = [action['id'] for action in candidates]
            with self.db.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                row = connection.execute('SELECT value FROM kv WHERE key=?', (receipt,)).fetchone()
                if row:
                    return json.loads(row['value'])
                self.expire(connection)
                for action in candidates:
                    action.pop('_rank', None)
                    connection.execute("UPDATE actions SET status='superseded' WHERE village_id=? AND type=? AND status='proposed' AND json_extract(data,'$.mode')=?", (action['village_id'], action['type'], action['mode']))
                    connection.execute('INSERT INTO actions(id,village_id,kind,type,status,benefit,approved_by,expires_at,observation_id,responder_id,anonymous,trace_id,data,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (action['id'], action['village_id'], action['kind'], action['type'], 'proposed', json.dumps(action['benefit'], allow_nan=False), None,
                         action['expires_at'], action['observation_id'], action['responder_id'], 0, action['trace_id'], json.dumps(action, allow_nan=False), action['created_at']))
                trace=saved.get('trace_id')
                record(self.db,'Verifier','evidence_checked',trace_id=trace,
                       input={'observation_id':saved['observation_id'],'village_id':observation['village_id'],'mode':observation['mode'],'day':observation['day']},
                       output={'decision':decision,'checks':checks},event_id='e_verifier_'+saved['observation_id'],connection=connection)
                record(self.db,'Planner','proposals_created',trace_id=trace,
                       input={'observation_id':saved['observation_id'],'mode':observation['mode'],'day':observation['day']},
                       output={'action_ids':ids},tool_calls=[{'name':'simulate_intervention',
                            'arguments':{'village_id':action['village_id'],**action['simulation_request']},
                            'result':{key:action['simulation'][key] for key in ('days_bought','at_least_days','reason','wt_gain_cm','pfvi_reduction','baseline_critical_date','scenario_critical_date')}}
                            for action in candidates if action['kind']=='proposal'],
                       event_id='e_planner_'+saved['observation_id'],connection=connection)
                connection.execute('INSERT INTO kv(key,value) VALUES(?,?)', (receipt, json.dumps(ids)))
            return ids

    def _base(self, observation, saved, action_type, kind, title, verifier) -> dict:
        return dict(id=identifier('a'), village_id=observation['village_id'], kind=kind, type=action_type,
                    status='proposed', title=title, reason='Laporan lapangan terkonfirmasi; pelaksanaan memerlukan persetujuan',
                    observation_id=saved['observation_id'], responder_id=observation['responder_id'], anonymous=False,
                    days_bought=None, at_least_days=None, benefit={'wt_gain_cm': None, 'pfvi_reduction': None},
                    verifier=verifier, created_at=self._now(), expires_at=(self.clock()+timedelta(hours=6)).isoformat().replace('+00:00','Z'),
                    trace_id=saved.get('trace_id'), approved_by=None, mode=observation['mode'], day=observation['day'])

    def list(self, village_id: str | None = None, include_old: bool = False) -> dict:
        with self.db.connect() as connection:
            self.expire(connection)
            rows = connection.execute('SELECT * FROM actions WHERE (? IS NULL OR village_id=?) ORDER BY created_at DESC,rowid DESC', (village_id,village_id)).fetchall()
        hidden = [row for row in rows if row['status'] in ('expired','superseded')]
        return {'items': [self._action(row) for row in rows if include_old or row['status'] not in ('expired','superseded')],
                'hidden_count': 0 if include_old else len(hidden)}

    def transition(self, action_id: str, operation: str, note: str = '') -> dict:
        with self._lock, self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self.expire(connection)
            row = connection.execute('SELECT * FROM actions WHERE id=?', (action_id,)).fetchone()
            if not row:
                raise KeyError('Unknown action')
            action = self._action(row)
            target = {'approve':'approved','reject':'rejected','complete':'done'}[operation]
            duplicate=row['status']==target or operation=='approve' and row['status']=='done'
            if not duplicate and row['status'] != ('approved' if operation == 'complete' else 'proposed'):
                raise HTTPException(409, 'Action cannot change from current status')
            if not duplicate:
                action.update(status=target,approved_by=('demo_operator' if self.channels.settings.public_demo else 'operator') if operation=='approve' else row['approved_by'])
                action[operation+'_note']=note
            if operation == 'approve':
                text = followup_text(action)
                if len(text) > 300:
                    raise ValueError('Follow-up exceeds message limit')
                followup_id = 'm_followup_' + action_id
                self.channels.enqueue(action['responder_id'],text,'sms',trace_id=action['trace_id'],idempotency_key=followup_id,connection=connection)
                action['followup_message_id'] = followup_id
            connection.execute('UPDATE actions SET status=?,approved_by=?,data=? WHERE id=?',
                               (action['status'], action['approved_by'], json.dumps(action, allow_nan=False), action_id))
            if not duplicate:
                record(self.db,'Operator',target,trace_id=action['trace_id'],input={'action_id':action_id,'mode':action['mode'],'day':action['day']},
                       output={'status':target},event_id='e_operator_'+operation+'_'+action_id,connection=connection)
        # Reservation and provider POST occur AFTER the approval/outbox transaction.
        # dispatch() never retries ambiguous/failed attempts, even across processes.
        if operation=='approve':
            delivery=self.channels.dispatch(action['followup_message_id'])
            action['followup_status']=delivery['status']
            with self.db.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                latest=connection.execute('SELECT * FROM actions WHERE id=?',(action_id,)).fetchone()
                action=self._action(latest)
                actual=connection.execute('SELECT status FROM messages WHERE id=?',(action['followup_message_id'],)).fetchone()['status']
                action['followup_status']=actual
                delivery['status']=actual
                connection.execute('UPDATE actions SET data=? WHERE id=?',(json.dumps(action,allow_nan=False),action_id))
                success=actual in ('mock','queued','sent','delivered')
                record(self.db,'Warden','followup_sent' if success else 'followup_unconfirmed',trace_id=action['trace_id'],
                       input={'action_id':action_id,'responder_id':action['responder_id']},output=delivery,
                       event_id='e_followup_'+action_id+'_'+actual,connection=connection)
        return action

    def progress(self, responder_id: str) -> dict:
        state = self.db.get('warden:' + responder_id, {})
        with self.db.connect() as connection:
            if not connection.execute('SELECT 1 FROM responders WHERE id=?', (responder_id,)).fetchone():
                raise KeyError('Unknown responder')
            observation = connection.execute('SELECT id FROM observations WHERE responder_id=? AND id=?', (responder_id,state.get('observation_id'))).fetchone()
            action = connection.execute('SELECT * FROM actions WHERE observation_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (observation['id'] if observation else None,)).fetchone()
            followup = connection.execute("SELECT id FROM messages WHERE id=? AND status IN ('mock','queued','sent','delivered')", ('m_followup_'+action['id'] if action else None,)).fetchone()
            counts = {table: connection.execute(f'SELECT COUNT(*) AS n FROM {table} WHERE responder_id=?', (responder_id,)).fetchone()['n'] for table in ('messages','observations','actions')}
        done = [bool(state.get('trace_id')), bool(observation), bool(action), bool(action and action['status'] in ('approved','done')), bool(followup)]
        return {'responder_id': responder_id, 'state': state.get('state','idle'), 'mode': state.get('mode','live'), 'day': state.get('day',0),
                'observation_id': observation['id'] if observation else None, 'action_id': action['id'] if action else None,
                'steps': [{'id': name, 'label': label, 'done': flag} for name,label,flag in zip(
                    ('alert','report','proposal','approved','followup'), ('Notifikasi','Laporan terkonfirmasi','Usulan','Persetujuan','Tindak lanjut'), done)],
                'counts': counts}
