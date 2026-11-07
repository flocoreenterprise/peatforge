"""Persisted risk crossings and deterministic autonomy, always mock delivery here."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import RLock
from engine.cache import DataUnavailable
from .channels import ChannelAdapter, ChannelUnavailable
from .db import Database, identifier
from .events import record
from .tools import ToolService
from .warden import Warden

LEVELS={'RENDAH':0,'SEDANG':1,'TINGGI':2,'EKSTREM':3}
WIB=timezone(timedelta(hours=7),'Asia/Jakarta')  # WIB has no daylight saving.
POLICY={'max_calls_per_day':1,'quiet_hours':[21,5],'timezone':'Asia/Jakarta',
        'default_channel':'sms','l3_requires_approval':True,'call_cooldown_seconds':30,
        'voice_min_level':'TINGGI','delivery':'mock'}


def decision(channel: str, level: str, now: datetime, *, calls_today: int = 0,
             last_call: float | None = None, hour_override: int | None = None,risk_override:bool=False) -> tuple[bool,str]:
    if level not in LEVELS or channel not in ('sms','voice'):
        raise ValueError('Invalid policy context')
    hour=now.astimezone(WIB).hour if hour_override is None else hour_override
    if not 0<=hour<=23:
        raise ValueError('Invalid hour')
    if (hour>=21 or hour<5) and level!='EKSTREM':
        return False,'quiet_hours'
    if channel=='voice':
        if LEVELS[level]<LEVELS['TINGGI'] and not risk_override:
            return False,'voice_below_high'
        if last_call is not None and now.timestamp()-last_call<30:
            return False,'call_cooldown'
        if calls_today>=1:
            return False,'daily_call_limit'
    return True,'allowed'


def reserve_call(database:Database,responder_id:str,level:str,now:datetime, *,risk_override:bool=False):
    """Reserve the hard daily attempt budget before any provider POST, across processes."""
    key='call-policy:'+responder_id
    today=now.astimezone(WIB).date().isoformat()
    with database.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row=connection.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
        state=json.loads(row['value']) if row else {}
        count=state.get('count',0) if state.get('date')==today else 0
        allowed,reason=decision('voice',level,now,calls_today=count,last_call=state.get('last_call'),risk_override=risk_override)
        if allowed:
            value=json.dumps({'date':today,'count':count+1,'last_call':now.timestamp()})
            connection.execute('INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
    return allowed,reason


def sms_policy(database:Database,tools:ToolService,responder_id:str,channel:str,now:datetime):
    if channel!='sms':
        return True,'allowed'
    with database.connect() as connection:
        row=connection.execute('SELECT village_id FROM responders WHERE id=?',(responder_id,)).fetchone()
    if not row:
        raise KeyError('Unknown responder')
    status=tools.get_village_status(row['village_id'],'live',0)
    return decision('sms',status.level,now)


class Scout:
    def __init__(self,database: Database,tools: ToolService,channels: ChannelAdapter,warden: Warden, *, clock=None):
        self.db,self.tools,self.channels,self.warden=database,tools,channels,warden
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self._lock=RLock()

    def _state_key(self,village_id,mode):
        return f'scout:{mode}:{village_id}'

    def _notify(self,status,state, *, hour_override=None,level_override=None,manual=False):
        actual=status.level
        policy_level=level_override or actual
        allowed,reason=decision('sms',policy_level,self.clock(),hour_override=hour_override)
        trace_id=state['trace_id']
        output={'village_id':status.village_id,'level':actual,'previous_level':state.get('previous_level'),
                'notified':0,'suppressed':0,'reason':reason,'trace_id':trace_id}
        active=self.db.get('scout:active-context',{})
        if status.mode=='live' and active.get('mode')=='replay' and self.clock().timestamp()-active.get('updated_at',0)<30:
            output['reason']='active_replay_context'
            return output
        with self.db.connect() as connection:
            responders=connection.execute("SELECT id FROM responders WHERE village_id=? AND role!='PUBLIC' ORDER BY tier,id",(status.village_id,)).fetchall()
        if not responders:
            output['reason']='no_responders'
            return output
        event_key=f"{status.mode}:{status.village_id}:{state['generation']}"
        if not allowed:
            output['suppressed']=len(responders)
            record(self.db,'Scout','suppressed',trace_id=trace_id,input={'village_id':status.village_id,'mode':status.mode,'day':status.day,
                   'level':actual,'policy_level':policy_level,'override':hour_override is not None or level_override is not None},
                   output={'decision':'suppress','reason':reason,'suppressed':len(responders)},event_id='e_scout_suppress_'+event_key+':'+reason)
            return output
        if status.pfvi is None or status.wt_cm is None:
            raise DataUnavailable('Alert facts unavailable')
        decrease=state.get('previous_level') is not None and LEVELS[actual]<LEVELS[state['previous_level']]
        text=(f'Simulasi notifikasi. Tingkat model turun ke {actual}. Perkiraan model, belum diukur.' if decrease else
              f'Simulasi notifikasi. Tingkat model {actual}; indeks {status.pfvi:g}; air {status.wt_cm:g} cm (perkiraan model, belum diukur). Balas AIR dalam cm dan ASAP YA atau TIDAK.')
        failed=False
        for responder in responders:
            receipt='scout-delivery:'+event_key+':'+responder['id']
            message_id='m_scout_'+hashlib.sha256(receipt.encode()).hexdigest()
            try:
                sent=self.channels.send(responder['id'],text,'sms',trace_id=trace_id,mock_only=True,idempotency_key=message_id)
            except ChannelUnavailable:
                failed=True
                output['reason']='delivery_unavailable'
                record(self.db,'Scout','delivery_failed',trace_id=trace_id,input={'village_id':status.village_id,'responder_id':responder['id'],'mode':status.mode,'day':status.day},
                       output={'reason':'delivery_unavailable'},event_id='e_scout_failed_'+message_id)
                continue
            if not self.db.get(receipt):
                self.warden.reset_for_alert(responder['id'],status.mode,status.day,trace_id)
                self.db.set(receipt,True)
                output['notified']+=1
                record(self.db,'Scout','alert_sent',trace_id=trace_id,input={'village_id':status.village_id,'mode':status.mode,'day':status.day,
                       'level':actual,'level_from':state.get('previous_level'),'policy_level':policy_level,'override':hour_override is not None or level_override is not None},
                       output={'responder_id':responder['id'],'message_id':sent['message_id'],'status':'mock','mock':True},event_id='e_'+message_id)
        state['pending']=failed
        if not failed:
            state['notified_level']=actual
        self.db.set(self._state_key(status.village_id,status.mode),state)
        return output

    def tick(self,mode='live',day=0, *, background=False):
        if mode not in ('live','replay') or not 0<=day<=61 or mode=='live' and day!=0:
            raise ValueError('Invalid scout context')
        if not background:
            self.db.set('scout:active-context',{'mode':mode,'day':day,'updated_at':self.clock().timestamp()})
        else:
            active=self.db.get('scout:active-context',{})
            if active.get('mode')=='replay' and self.clock().timestamp()-active.get('updated_at',0)<30:
                return {'mode':mode,'day':day,'items':[],'notified':0,'suppressed':0,'rewound':False,'reason':'active_replay_context'}
        # Fetching/model computation happens in the caller's worker thread, not ASGI loop.
        statuses=[]
        errors=[]
        from engine.villages import list_villages
        for village in list_villages():
            try:
                statuses.append(self.tools.get_village_status(village['id'],mode,day))
            except DataUnavailable:
                errors.append({'village_id':village['id'],'reason':'unavailable','notified':0,'suppressed':0})
        with self._lock:
            active=self.db.get('scout:active-context',{})
            if not background and (active.get('mode'),active.get('day'))!=(mode,day):
                return {'mode':mode,'day':day,'items':[],'notified':0,'suppressed':0,'rewound':False,'reason':'stale_context'}
            context_key='scout-cursor:'+mode
            cursor=self.db.get(context_key)
            rewind=mode=='replay' and cursor is not None and day<cursor
            items=[]
            for status in statuses:
                key=self._state_key(status.village_id,mode)
                state=self.db.get(key,{})
                previous=state.get('level')
                generation=state.get('generation',0)
                if rewind:
                    state.update(level=status.level,pending=False,generation=generation+1,day=day)
                    self.db.set(key,state)
                    record(self.db,'Scout','replay_rewind',trace_id=None,input={'village_id':status.village_id,'mode':mode,'day':day},output={'rewound':True})
                    items.append({'village_id':status.village_id,'level':status.level,'previous_level':previous,'notified':0,'suppressed':0,'reason':'rewind_baseline_reset'})
                    continue
                crossing=(previous is None and LEVELS[status.level]>=2 or previous is not None and
                          (LEVELS[status.level]>=2 and LEVELS[status.level]>LEVELS[previous] or LEVELS[status.level]<LEVELS[previous]))
                if crossing:
                    state.update(pending=True,generation=generation+1,previous_level=previous,trace_id=identifier('t'))
                state.update(level=status.level,day=day)
                self.db.set(key,state)
                if state.get('pending'):
                    try:
                        items.append(self._notify(status,state))
                    except (ChannelUnavailable,DataUnavailable):
                        items.append({'village_id':status.village_id,'level':status.level,'notified':0,'suppressed':0,'reason':'delivery_unavailable','trace_id':state['trace_id']})
                        record(self.db,'Scout','delivery_failed',trace_id=state['trace_id'],input={'village_id':status.village_id,'mode':mode,'day':day},
                               output={'reason':'delivery_unavailable'},event_id='e_scout_failed_'+state['trace_id'])
                else:
                    items.append({'village_id':status.village_id,'level':status.level,'previous_level':previous,'notified':0,'suppressed':0,'reason':'no_crossing'})
            if statuses and not errors:
                self.db.set(context_key,day)
            items.extend(errors)
            return {'mode':mode,'day':day,'items':items,'notified':sum(item['notified'] for item in items),
                    'suppressed':sum(item['suppressed'] for item in items),'rewound':rewind}

    def manual(self,village_id,mode='live',day=0, *, hour_override=None,level_override=None):
        status=self.tools.get_village_status(village_id,mode,day)
        with self._lock:
            self.db.set('scout:active-context',{'mode':mode,'day':day,'updated_at':self.clock().timestamp()})
            key=self._state_key(village_id,mode)
            previous=self.db.get(key,{})
            state={'level':status.level,'previous_level':previous.get('level'),'generation':previous.get('generation',0)+1,
                   'pending':True,'trace_id':identifier('t'),'day':day}
            self.db.set(key,state)
            result=self._notify(status,state,hour_override=hour_override,level_override=level_override,manual=True)
            return {**result,'mock':True,'level':status.level,'source':status.source,'mode':mode,'day':day,
                    'assumptions':['Pemicu demo manual; override hanya untuk kebijakan kanal simulasi, tidak mengubah fakta model']}

    def place_call(self,responder_id,mode='live',day=0):
        with self._lock:
            with self.db.connect() as connection:
                row=connection.execute('SELECT village_id FROM responders WHERE id=?',(responder_id,)).fetchone()
            if not row:
                raise KeyError('Unknown responder')
            status=self.tools.get_village_status(row['village_id'],mode,day)
            now=self.clock()
            allowed,reason=reserve_call(self.db,responder_id,status.level,now)
            trace=self.db.get('warden:'+responder_id,{}).get('trace_id') or identifier('t')
            if not allowed:
                record(self.db,'Scout','call_suppressed',trace_id=trace,input={'responder_id':responder_id,'mode':mode,'day':day,'level':status.level},output={'reason':reason,'decision':'suppress'})
                return {'call_id':None,'status':'suppressed','reason':reason}
            call_id=identifier('call')
            self.channels.send(responder_id,'Simulasi panggilan Warden. Tidak ada panggilan telepon nyata.','voice',trace_id=trace,mock_only=True)
            record(self.db,'Scout','call_mocked',trace_id=trace,input={'responder_id':responder_id,'mode':mode,'day':day,'level':status.level},output={'call_id':call_id,'status':'mock','mock':True})
            return {'call_id':call_id,'status':'mock','reason':'allowed'}
