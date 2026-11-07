"""Structured audit events. Free text, contacts, credentials and URLs are excluded."""
import json
from .db import Database, identifier, utc_now

_KEYS = {'village_id','responder_id','mode','day','level','level_from','level_to','policy_level',
         'decision','reason','source','status','observation_id','action_id','action_ids','message_id','call_id',
         'notified','suppressed','mock','rewound','difference_cm','wt_cm','pfvi','days_bought','at_least_days',
         'wt_gain_cm','pfvi_reduction','checks','name','passed','available','detections','limit_cm',
         'model_wt_cm','observed_wt_cm','override','hour','channel','state','version','recalibrated',
         'arguments','result','input','output','type','targets','delta_hc_cm','days',
         'baseline_critical_date','scenario_critical_date','diagnostic','duration_seconds','turn','uat','error_code'}


def safe_fields(value):
    if isinstance(value,dict):
        return {key:safe_fields(item) for key,item in value.items() if key in _KEYS}
    if isinstance(value,list):
        return [safe_fields(item) for item in value[:100]]
    if value is None or isinstance(value,(bool,int,float)):
        return value
    if isinstance(value,str):
        # All event strings are controlled enums, IDs or fixed reasons, not user text.
        if len(value)>160 or '://' in value or '/f/' in value or '+' in value:
            return '[redacted]'
        return value
    return None


def record(database: Database, agent: str, event: str, *, trace_id: str | None,
           input: dict | None = None, output: dict | None = None, tool_calls: list | None = None,
           event_id: str | None = None, connection=None) -> None:
    values=(event_id or identifier('e'),agent,event,json.dumps(safe_fields(input or {}),allow_nan=False),
            json.dumps(safe_fields(output or {}),allow_nan=False),json.dumps(safe_fields(tool_calls or []),allow_nan=False),trace_id,utc_now())
    def insert(conn):
        conn.execute('INSERT OR IGNORE INTO agent_events(id,agent,event,input,output,tool_calls,trace_id,created_at) VALUES(?,?,?,?,?,?,?,?)',values)
    if connection is not None:
        insert(connection)
    else:
        with database.connect() as conn:
            insert(conn)


def list_events(database: Database, trace_id: str | None = None, after: str | None = None) -> list[dict]:
    with database.connect() as connection:
        rows=connection.execute('SELECT * FROM (SELECT * FROM agent_events WHERE (? IS NULL OR trace_id=?) AND (? IS NULL OR created_at>?) ORDER BY created_at DESC,rowid DESC LIMIT 200) ORDER BY created_at',
                                (trace_id,trace_id,after,after)).fetchall()
    return [{**dict(row),**{key:safe_fields(json.loads(row[key])) for key in ('input','output','tool_calls')}} for row in rows]
