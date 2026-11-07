"""Scoped anonymous public text and server-capped voice reporting."""
import hashlib
import json
import re
import secrets
import time
import asyncio
from threading import RLock
from fastapi import APIRouter,HTTPException,Request
from pydantic import Field,StrictBool,model_validator
from engine.villages import list_villages
from .db import identifier,utc_now
from .models import Model
from .persona import greeting
from .realtime import Access
from typing import Literal

class VoiceEnd(Model):
    session_id:str=Field(min_length=1,max_length=80)
class VoiceConnect(VoiceEnd):
    sdp:str=Field(min_length=3,max_length=65536)
class VoiceTranscript(VoiceEnd):
    event_id:str=Field(min_length=1,max_length=128)
    role:Literal['user','assistant']
    text:str=Field(min_length=1,max_length=500)
class VoiceTool(VoiceEnd):
    call_id:str=Field(min_length=1,max_length=128)
    name:str=Field(min_length=1,max_length=80)
    arguments:dict=Field(default_factory=dict)

class Start(Model):
    village_id:str=Field(min_length=1,max_length=80)
    consent:StrictBool
    @model_validator(mode='after')
    def agreed(self):
        if not self.consent:
            raise ValueError('Consent required')
        return self

class Message(Model):
    text:str=Field(min_length=1,max_length=500)
    event_id:str=Field(min_length=1,max_length=128)

class PublicReports:
    def __init__(self,app,*,clock=time.time):
        self.app,self.db,self.clock=app,app.state.database,clock
        self.lock=RLock()
    def enabled(self):
        if not self.app.state.config.get('public_reporting_enabled',False):
            raise HTTPException(404,'Not found')
    def budget(self,connection,key,limit,seconds):
        now=self.clock()
        key='public-budget:'+key+':'+str(int(now//seconds))
        row=connection.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
        value=json.loads(row['value']) if row else 0
        if value>=limit:
            raise HTTPException(429,'Please try later')
        connection.execute('INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value+1)))
    def start(self,village_id,peer):
        self.enabled()
        village=next((v for v in list_villages() if v['id']==village_id),None)
        if not village:
            raise HTTPException(422,'Unknown village')
        token=secrets.token_urlsafe(32)
        digest=hashlib.sha256(token.encode()).hexdigest()
        rid=identifier('public')
        state=dict(responder_id=rid,mode='live',day=0,trace_id=identifier('t'),stage='consent',turn=0,empty=0,rounds=0,report={},diagnostic=False)
        session={'responder_id':rid,'expires_at':self.clock()+86400,'state':state,'events':{},'ended':False}
        text=greeting(None,village['name'])
        with self.lock,self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self.budget(connection,'global',5,60)
            self.budget(connection,'peer:'+hashlib.sha256(peer.encode()).hexdigest(),2,60)
            self.budget(connection,'daily',20,86400)
            connection.execute("INSERT INTO responders(id,village_id,name,role) VALUES(?,?,?,'PUBLIC')",(rid,village_id,'Laporan publik'))
            connection.execute('INSERT INTO kv(key,value) VALUES(?,?)',('public-session:'+digest,json.dumps(session)))
            connection.execute('INSERT INTO messages(id,responder_id,direction,channel,body,status,trace_id,created_at) VALUES(?,?,?,?,?,?,?,?)',(identifier('m'),rid,'outbound','web',text,'mock',state['trace_id'],utc_now()))
        return dict(token=token,**self.view(session))
    def authenticate(self,request):
        self.enabled()
        header=request.headers.get('Authorization','')
        if not re.fullmatch(r'Bearer [A-Za-z0-9_-]{43}',header):
            raise HTTPException(401,'Reporting token required')
        digest=hashlib.sha256(header[7:].encode()).hexdigest()
        session=self.db.get('public-session:'+digest)
        if not session or session['expires_at']<=self.clock():
            raise HTTPException(401,'Reporting token expired or invalid')
        return digest,session
    def view(self,session):
        rid=session['responder_id']
        with self.db.connect() as connection:
            rows=connection.execute('SELECT id,direction,body,status,created_at FROM messages WHERE responder_id=? ORDER BY created_at,rowid',(rid,)).fetchall()
            actions=connection.execute('SELECT id,status,data FROM actions WHERE responder_id=? ORDER BY created_at',(rid,)).fetchall()
        safe_actions=[]
        for row in actions:
            data=json.loads(row['data'])
            safe_actions.append({k:data.get(k) for k in ('id','title','status','priority','source','anonymous','verifier','approved_by','followup_status')})
            safe_actions[-1]['status']=row['status']
        warden=self.db.get('warden:'+rid,{})
        state=warden.get('state','collecting')
        return dict(items=[dict(row) for row in rows],actions=safe_actions,state=state,stage=session['state']['stage'],ended=session.get('ended',False),voice_available=bool(self.app.state.demo_public),source='public')
    def send(self,digest,session,text,event_id):
        with self.lock:
            session=self.db.get('public-session:'+digest)
            receipt=session['events'].get(event_id)
            fingerprint=hashlib.sha256(text.encode()).hexdigest()
            if receipt:
                if receipt!=fingerprint:
                    raise HTTPException(409,'Message identity conflict')
                return self.view(session)
            if session.get('ended'):
                raise HTTPException(409,'Reporting session ended')
            if len(session['events'])>=200:
                raise HTTPException(429,'Reporting session limit reached')
            with self.db.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                self.budget(connection,'token:'+digest,30,60)
            turn=session['state']['turn']
            rawid='m_public_'+digest+'_'+str(turn)
            self.app.state.channels.record_voice(session['responder_id'],text,direction='inbound',idempotency_key=rawid,trace_id=session['state']['trace_id'])
            response=self.app.state.voice._turn(session['state'],text,'public_'+digest,turn)
            if not response.get('message_id'):
                self.app.state.channels.record_voice(session['responder_id'],response['text'],idempotency_key=rawid+'_reply',trace_id=session['state']['trace_id'])
            session['state']['turn']=turn+1
            session['ended']=response.get('hangup',False)
            session['events'][event_id]=fingerprint
            self.db.set('public-session:'+digest,session)
            return self.view(session)

def router():
    routes=APIRouter(prefix='/api/public')
    def voice_access(request):
        digest,session=request.app.state.public_reports.authenticate(request)
        if not request.app.state.demo_public:
            raise HTTPException(503,'Public voice unavailable; please use text')
        with request.app.state.database.connect() as connection:
            responder=connection.execute('SELECT village_id FROM responders WHERE id=?',(session['responder_id'],)).fetchone()
        if not responder:
            raise HTTPException(401,'Reporting session unavailable')
        return Access('public:'+digest,session['responder_id'],responder['village_id'],'live',0,True)
    @routes.get('/villages')
    def villages(request:Request):
        request.app.state.public_reports.enabled()
        return {'items':[{k:v[k] for k in ('id','name','lat','lon')} for v in list_villages()]}
    @routes.post('/sessions')
    def start(request:Request,body:Start):
        peer=request.client.host if request.client else 'unknown'
        return request.app.state.public_reports.start(body.village_id,peer)
    @routes.get('/messages')
    def messages(request:Request):
        service=request.app.state.public_reports
        _,session=service.authenticate(request)
        return service.view(session)
    @routes.post('/messages')
    def send(request:Request,body:Message):
        service=request.app.state.public_reports
        digest,session=service.authenticate(request)
        return service.send(digest,session,body.text,body.event_id)
    @routes.post('/voice/session')
    async def voice(request:Request):
        access=voice_access(request)
        return await asyncio.to_thread(request.app.state.browser_voice.mint,access)
    @routes.post('/voice/connect')
    async def connect(request:Request,body:VoiceConnect):
        access=voice_access(request)
        return await asyncio.to_thread(request.app.state.browser_voice.connect,access,body.session_id,body.sdp)
    @routes.post('/voice/transcript')
    def transcript(request:Request,body:VoiceTranscript):
        access=voice_access(request)
        return request.app.state.browser_voice.transcript(access,body.session_id,body.event_id,body.role,body.text)
    @routes.post('/voice/tool')
    def tool(request:Request,body:VoiceTool):
        access=voice_access(request)
        return request.app.state.browser_voice.tool(access,body.session_id,body.call_id,body.name,body.arguments)
    @routes.post('/voice/end')
    def end(request:Request,body:VoiceEnd):
        access=voice_access(request)
        return request.app.state.browser_voice.end(access,body.session_id)
    return routes
