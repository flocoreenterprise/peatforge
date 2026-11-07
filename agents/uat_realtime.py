"""Single explicitly authorized Realtime UAT after the already-completed test pair."""
import json
from .scout import WIB, decision


def reserve_realtime_uat(database, responder_id, level, now):
    today=now.astimezone(WIB).date().isoformat()
    key='call-policy:'+responder_id
    receipt='uat-realtime:'+responder_id
    with database.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        def read(name):
            row=connection.execute('SELECT value FROM kv WHERE key=?',(name,)).fetchone()
            return json.loads(row['value']) if row else {}
        state,pair,used=read(key),read('uat-pair:'+responder_id),read(receipt)
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
