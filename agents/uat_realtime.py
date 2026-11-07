"""Single explicitly authorized Realtime UAT after the already-completed test pair."""
import json
from datetime import datetime
from .scout import WIB, decision


def reserve_realtime_uat(database, responder_id, level, now, *, retry_rejected=False, stream_repair=False):
    if retry_rejected and stream_repair:
        return False,'realtime_uat_flags_conflict'
    today=now.astimezone(WIB).date().isoformat()
    key='call-policy:'+responder_id
    receipt='uat-realtime:'+responder_id
    with database.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        def read(name):
            row=connection.execute('SELECT value FROM kv WHERE key=?',(name,)).fetchone()
            return json.loads(row['value']) if row else {}
        state,pair,used=read(key),read('uat-pair:'+responder_id),read(receipt)
        if stream_repair:
            repair_key='uat-stream-repair:'+responder_id
            if (used.get('date')!=today or state.get('date')!=today or state.get('count')!=3
                    or pair.get('date')!=today or pair.get('count')!=2 or pair.get('phase')!='complete'
                    or read(repair_key).get('date')==today):
                return False,'stream_repair_uat_unavailable'
            records=connection.execute("SELECT input,output,created_at FROM agent_events WHERE event='call_requested' ORDER BY created_at DESC,rowid DESC").fetchall()
            attempt=next((row for row in records if json.loads(row['input']).get('responder_id')==responder_id),None)
            if not attempt or datetime.fromisoformat(attempt['created_at'].replace('Z','+00:00')).timestamp()<used.get('reserved_at',float('inf')):
                return False,'stream_repair_requires_completed_call'
            call_id=json.loads(attempt['output']).get('call_id')
            sessions=[json.loads(row['value']) for row in connection.execute("SELECT value FROM kv WHERE key LIKE 'voice-session:%'")]
            previous=next((entry for entry in sessions if entry.get('call_id')==call_id),None)
            if not previous or previous.get('provider_status')!='completed':
                return False,'stream_repair_requires_completed_call'
            terminal={'completed','busy','failed','no-answer','canceled'}
            if any((entry['provider_status'] not in terminal if entry.get('provider_status')
                    else entry.get('status')!='failed') for entry in sessions):
                return False,'stream_repair_active_call'
            allowed,reason=decision('voice',level,now,calls_today=0,last_call=state.get('last_call'),risk_override=True)
            if not allowed:
                return False,reason
            state.update(count=4,last_call=now.timestamp())
            connection.execute('UPDATE kv SET value=? WHERE key=?',(json.dumps(state),key))
            connection.execute('INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (repair_key,json.dumps({'date':today,'reserved_at':now.timestamp(),'previous_call_id':call_id})))
            return True,'allowed_one_stream_repair_uat'
        if retry_rejected:
            if used.get('date')!=today or used.get('rejected_retry_used') or state.get('date')!=today or state.get('count')!=3:
                return False,'realtime_retry_unavailable'
            records=connection.execute("SELECT input,output,created_at FROM agent_events WHERE event='call_requested' ORDER BY created_at DESC,rowid DESC").fetchall()
            attempt=next((row for row in records if json.loads(row['input']).get('responder_id')==responder_id),None)
            if not attempt or datetime.fromisoformat(attempt['created_at'].replace('Z','+00:00')).timestamp()<used.get('reserved_at',float('inf')):
                return False,'realtime_retry_no_rejected_attempt'
            output=json.loads(attempt['output'])
            states=[json.loads(row['value']) for row in connection.execute("SELECT value FROM kv WHERE key LIKE 'voice-session:%'")]
            failed=next((entry for entry in states if entry.get('call_id')==output.get('call_id')),None)
            # 'failed' is assigned only for definitive HTTP4xx; transport/5xx ambiguity is 'unknown'.
            activity=failed and (any(failed.get(name) for name in ('provider_hash','provider_status','provider_callback_received',
                'realtime_claimed','realtime_deadline','realtime_terminal','last_response','responses')) or failed.get('turn',0)>0)
            if output.get('status')!='failed' or not failed or failed.get('status')!='failed' or activity:
                return False,'realtime_retry_not_definitively_rejected'
            allowed,reason=decision('voice',level,now,calls_today=0,last_call=state.get('last_call'),risk_override=True)
            if not allowed:
                return False,reason
            used['rejected_retry_used']=True
            used['rejected_call_id']=output['call_id']
            state['last_call']=now.timestamp()
            for name,value in ((key,state),(receipt,used)):
                connection.execute('UPDATE kv SET value=? WHERE key=?',(json.dumps(value),name))
            return True,'allowed_manual_retry_rejected_create'
        if used.get('date')==today:
            return False,'realtime_uat_used'
        if (state.get('date')!=today or state.get('count')!=2 or pair.get('date')!=today
                or pair.get('count')!=2 or pair.get('phase')!='complete'):
            return False,'realtime_uat_requires_completed_pair'
        allowed,reason=decision('voice',level,now,calls_today=0,last_call=state.get('last_call'),risk_override=True)
        if not allowed:
            return False,reason
        for name,value in ((key,{'date':today,'count':3,'last_call':now.timestamp()}),
                           (receipt,{'date':today,'reserved_at':now.timestamp()})):
            connection.execute('INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                               (name,json.dumps(value)))
    return True,'allowed_one_realtime_uat'
