from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
from agents.db import Database
from agents.uat_realtime import reserve_realtime_uat


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
