from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
from agents.db import Database
from agents.uat_realtime import reserve_realtime_uat
from agents.events import record


def prepared(tmp_path):
    db=Database(tmp_path/'calls.sqlite3'); db.initialize()
    now=datetime(2026,11,7,6,tzinfo=timezone.utc)
    db.set('call-policy:chosen',{'date':'2026-11-07','count':2,'last_call':now.timestamp()-60})
    db.set('uat-pair:chosen',{'date':'2026-11-07','count':2,'phase':'complete'})
    return db,now


def test_one_atomic_extra_attempt_survives_restart(tmp_path):
    db,now=prepared(tmp_path)
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda _:reserve_realtime_uat(db,'chosen','RENDAH',now),range(4)))
    assert sum(result[0] for result in results)==1
    assert db.get('call-policy:chosen')['count']==3
    assert not reserve_realtime_uat(Database(db.path),'chosen','EKSTREM',now+timedelta(minutes=1))[0]


def test_no_pair_cooldown_quiet_and_new_day_do_not_bypass_policy(tmp_path):
    db,now=prepared(tmp_path)
    assert not reserve_realtime_uat(db,'unrelated','EKSTREM',now)[0]
    db.set('call-policy:chosen',{'date':'2026-11-07','count':2,'last_call':now.timestamp()-5})
    assert not reserve_realtime_uat(db,'chosen','RENDAH',now)[0]
    assert not reserve_realtime_uat(db,'chosen','TINGGI',now.replace(hour=15))[0]
    assert not reserve_realtime_uat(db,'chosen','EKSTREM',now+timedelta(days=1))[0]


def test_one_manual_retry_only_for_definitive_failed_create_without_provider_sid(tmp_path):
    db,now=prepared(tmp_path)
    assert reserve_realtime_uat(db,'chosen','RENDAH',now)[0]
    record(db,'Warden','call_requested',trace_id=None,input={'responder_id':'chosen'},output={'call_id':'rejected','status':'failed'})
    db.set('voice-session:fake',{'call_id':'rejected','status':'failed'})
    assert reserve_realtime_uat(db,'chosen','RENDAH',now+timedelta(seconds=61),retry_rejected=True)[0]
    assert db.get('call-policy:chosen')['count']==3
    assert not reserve_realtime_uat(db,'chosen','RENDAH',now+timedelta(seconds=122),retry_rejected=True)[0]


def test_manual_retry_never_accepts_ambiguous_or_accepted_provider_attempt(tmp_path):
    db,now=prepared(tmp_path)
    assert reserve_realtime_uat(db,'chosen','RENDAH',now)[0]
    record(db,'Warden','call_requested',trace_id=None,input={'responder_id':'chosen'},output={'call_id':'unknown','status':'unknown'})
    db.set('voice-session:fake',{'call_id':'unknown','status':'unknown'})
    assert not reserve_realtime_uat(db,'chosen','RENDAH',now+timedelta(seconds=61),retry_rejected=True)[0]


def test_manual_retry_rejects_callback_or_stream_activity_even_without_hash(tmp_path):
    db,now=prepared(tmp_path)
    assert reserve_realtime_uat(db,'chosen','RENDAH',now)[0]
    record(db,'Warden','call_requested',trace_id=None,input={'responder_id':'chosen'},output={'call_id':'failed','status':'failed'})
    for extra in ({'provider_status':'in-progress'},{'provider_status':'completed'},{'provider_callback_received':True},
                  {'realtime_claimed':True},{'realtime_deadline':now.timestamp()+180},{'turn':1}):
        db.set('voice-session:fake',{'call_id':'failed','status':'failed',**extra})
        assert not reserve_realtime_uat(db,'chosen','RENDAH',now+timedelta(seconds=61),retry_rejected=True)[0]
    record(db,'Warden','call_requested',trace_id=None,input={'responder_id':'chosen'},output={'call_id':'accepted','status':'failed'})
    db.set('voice-session:fake2',{'call_id':'accepted','status':'failed','provider_hash':'already-created'})
    assert not reserve_realtime_uat(db,'chosen','RENDAH',now+timedelta(seconds=61),retry_rejected=True)[0]
