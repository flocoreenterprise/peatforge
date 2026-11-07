"""Public WebRTC: one-use ephemeral handshake and server-owned call deadlines.

Ephemeral credentials stay in memory: clients cannot reuse them for untracked calls.
Only the SDP answer reaches the client. The provider call ID enables server hangup.
"""
import json
import re
from threading import Timer
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from .channels import ChannelUnavailable
from .realtime import BrowserVoice


class DemoVoice(BrowserVoice):
    def __init__(self,*args,timer_factory=Timer,**kwargs):
        super().__init__(*args,lifetime=600,**kwargs)
        self.pending={}
        self.timers={}
        self.timer_factory=timer_factory
        self.closed=False
        # Restart never forgets an unfinished provider call or resets the budget.
        self.active=self.db.get('demo-voice-calls',{})
        if not self.active:
            self.db.set('demo-voice-hangup-failed',False)
        for sid in list(self.active):
            self._terminate(sid)

    def _budget(self):
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row=connection.execute("SELECT value FROM kv WHERE key='demo-voice-budget'").fetchone()
            entries=[v for v in (json.loads(row['value']) if row else []) if v>self.clock()-3600]
            if len(entries)>=200 or self.db.get('demo-voice-hangup-failed',False):
                raise HTTPException(429,'Demo voice limit reached')
            entries.append(self.clock())
            connection.execute("INSERT INTO kv(key,value) VALUES('demo-voice-budget',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(entries),))

    def mint(self,access):
        with self._lock:
            if self.closed:
                raise ChannelUnavailable('Voice unavailable')
            self._budget()
        result=super().mint(access)
        with self._lock:
            sid=result['session_id']
            self.pending[sid]=(result.pop('client_secret'),result['expires_at'])
            timer=self.timer_factory(max(0,result['session_expires_at']-self.clock()),lambda:self._terminate(sid))
            timer.daemon=True
            self.timers[sid]=timer
            timer.start()
        return {**result,'server_connect':True,'max_seconds':600}

    def connect(self,access,sid,sdp):
        if not sdp.startswith('v=0') or len(sdp)>65536:
            raise HTTPException(422,'Invalid SDP')
        with self._lock:
            session=self._load(sid,access)
            credential=self.pending.pop(sid,None)
            if not credential or credential[1]<=self.clock() or self.closed:
                raise HTTPException(409,'Session already connected or expired')
        call_id=None
        try:
            with httpx.Client(transport=self.transport,timeout=12,follow_redirects=False,trust_env=False) as client:
                response=client.post('https://api.openai.com/v1/realtime/calls',content=sdp,
                    headers={'Authorization':'Bearer '+credential[0],'Content-Type':'application/sdp'})
            credential=None
            path=urlsplit(response.headers.get('Location','')).path
            match=re.fullmatch(r'/v1/realtime/calls/([A-Za-z0-9_-]{1,200})',path)
            if match:
                call_id=match[1]
                with self._lock:
                    self.active[sid]=call_id
                    self.db.set('demo-voice-calls',self.active)
            if response.status_code not in (200,201) or not call_id or len(response.content)>65536 or not response.text.startswith('v=0'):
                raise ValueError('Invalid provider answer')
            with self._lock:
                current=self.db.get(self._key(sid),{})
                if self.closed or current.get('ended') or session['expires_at']<=self.clock():
                    raise ValueError('Expired during handshake')
            return {'sdp':response.text}
        except (httpx.HTTPError,ValueError):
            self._terminate(sid)
            raise ChannelUnavailable('Voice connection unavailable') from None

    def _terminate(self,sid):
        with self._lock:
            self.pending.pop(sid,None)
            timer=self.timers.pop(sid,None)
            if timer:
                timer.cancel()
            session=self.db.get(self._key(sid))
            if session:
                session['ended']=True
                self._save(session)
            call_id=self.active.get(sid)
        if not call_id:
            return
        success=False
        for attempt in range(3):
            try:
                with httpx.Client(transport=self.transport,timeout=4,follow_redirects=False,trust_env=False) as client:
                    response=client.post('https://api.openai.com/v1/realtime/calls/'+call_id+'/hangup',
                        headers={'Authorization':'Bearer '+self.settings.key})
                if response.status_code in (200,204,404,410):
                    success=True
                    break
            except httpx.HTTPError:
                pass
        with self._lock:
            if success:
                self.active.pop(sid,None)
            self.db.set('demo-voice-calls',self.active)
            if not success or not self.active:
                self.db.set('demo-voice-hangup-failed',bool(not success and self.active))
            if not success and not self.closed:
                timer=self.timer_factory(5,lambda:self._terminate(sid))
                timer.daemon=True
                self.timers[sid]=timer
                timer.start()

    def end(self,access,session_id):
        with self._lock:
            session=self.db.get(self._key(session_id))
            if not session or session['owner']!=access.owner or session['responder_id']!=access.responder_id:
                raise HTTPException(403,'Voice scope mismatch')
        self._terminate(session_id)
        return {'ended':True}

    def close(self):
        self.closed=True
        for sid in set(self.pending)|set(self.active)|set(self.timers):
            self._terminate(sid)
