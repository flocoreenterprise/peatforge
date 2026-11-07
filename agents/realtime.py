"""User-started browser voice: ephemeral credentials, scoped tools, final-text gates."""
from dataclasses import dataclass,field
from decimal import Decimal,InvalidOperation
import hashlib
import json
import os
import re
from threading import RLock
import time
from typing import Any
import httpx
from fastapi import HTTPException
from .auth import RateLimit
from .channels import ChannelUnavailable
from .db import identifier
from .events import record
from .voice import BYE
from .warden import _NUMBER_WORDS,number_words,message_items

MODEL='gpt-realtime-2.1'
VOICE='marin'
TTL=15*60
NAMES=('get_village_status','record_report','confirm_report','end_call')
INSTRUCTIONS='''Kamu Warden, asisten AI uji coba Peatforge, bukan petugas resmi BPBD. Bahasa Indonesia sehari-hari, sopan, singkat. Mulai dengan get_village_status dan ucapkan text persis: identitas, tujuan, lalu izin Ada waktu sebentar? SATU pertanyaan per giliran. Sesudah izin, tanya pengamatan air (kering/sedikit/masih ada/banjir), asap (tidak/bau/terlihat/api), lalu nama tempat bebas seperti handil/parit/kebun/ladang/beje. Jangan meminta angka, kedalaman, satuan atau pengukuran. Angka sukarela hanya diproses server. Jangan mengubah kategori menjadi angka. Setelah transkrip final pada tahap consent/wt/smoke/landmark panggil record_report argumen kosong. Ucapkan text tool persis; jangan menambah fakta, janji atau hitungan. Pada stage confirm bacakan text persis, tunggu transkrip BARU YA/IYA/TIDAK tersendiri, lalu confirm_report argumen kosong. ASAP YA bukan konfirmasi. Pamit udahan/matiin/nanti saja/lagi sibuk selalu end_call, tidak pernah YA. Jika terlihat api, ucapkan instruksi keselamatan server; jangan menyuruh memadamkan sendiri. Penutupan dan tindak lanjut bersyarat hanya dari server. Jangan meminta data pribadi atau menjanjikan anonim. Aksi lapangan memerlukan persetujuan operator. Identitas/konteks ditentukan server.'''



@dataclass(frozen=True)
class Access:
    owner:str
    responder_id:str
    village_id:str
    mode:str
    day:int
    field:bool=False


@dataclass(repr=False)
class Settings:
    key:str=field(default='',repr=False)
    model:str=MODEL
    voice:str=VOICE

    @classmethod
    def from_environment(cls):
        return cls(os.environ.get('OPENAI_API_KEY','').strip(),os.environ.get('OPENAI_REALTIME_MODEL','').strip() or MODEL,
                   os.environ.get('OPENAI_REALTIME_VOICE','').strip() or VOICE)


def numeric_tokens(text):
    values=set()
    for value in re.findall(r'[+-]?\d+(?:[.,]\d+)*',str(text)):
        try:
            values.add(str(Decimal(value.replace(',','.')).normalize()))
        except InvalidOperation:
            values.add(value)
    words=re.findall(r'[a-z]+',str(text).lower())
    phrase=[]
    for word in words+['END']:
        if word in _NUMBER_WORDS:
            phrase.append(word)
        else:
            if phrase:
                try:
                    values.add(str(Decimal(str(number_words(phrase))).normalize()))
                except (ValueError,KeyError,InvalidOperation):
                    pass
            phrase=[]
    english={'zero':0,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,
             'ten':10,'eleven':11,'twelve':12,'thirteen':13,'fourteen':14,'fifteen':15,'sixteen':16,'seventeen':17,
             'eighteen':18,'nineteen':19,'twenty':20,'thirty':30,'forty':40,'fifty':50,'sixty':60,'seventy':70,'eighty':80,'ninety':90}
    current=None
    for word in words+['END']:
        if word in english:
            current=(current or 0)+english[word]
        elif word=='hundred':
            current=(current or 1)*100
        else:
            if current is not None:
                values.add(str(Decimal(current).normalize()))
            current=None
    if 'setengah' in words:
        values.add('0.5')
    return values


def fact_numbers(value):
    if isinstance(value,dict):
        return set().union(*(fact_numbers(item) for item in value.values())) if value else set()
    if isinstance(value,list):
        return set().union(*(fact_numbers(item) for item in value)) if value else set()
    if value is None or isinstance(value,bool):
        return set()
    return numeric_tokens(value)


_ENGLISH_NUMBERS=set('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion trillion half quarter third fraction'.split())


def has_numeric_claim(text):
    words=re.findall(r'[a-z]+',text.lower())
    return bool(re.search(r'\d',text) or any(word in _NUMBER_WORDS or word in _ENGLISH_NUMBERS or
                re.fullmatch(r'(?:se)?(?:ribu|juta|miliar|triliun)|seper[a-z]+|setengah|persen|pecahan',word) for word in words))


def normalized_script(text):
    """Punctuation/unit differences and Indonesian spoken digits normalize, facts/order do not."""
    tokens=re.findall(r'[+-]?\d+(?:[.,]\d+)*|[a-z]+',text.lower())
    result=[]
    phrase=[]
    def flush():
        if phrase:
            try:
                result.append(str(Decimal(str(number_words(phrase))).normalize()))
            except (ValueError,KeyError,InvalidOperation):
                result.extend(phrase)
            phrase.clear()
    for token in tokens:
        if token in _NUMBER_WORDS:
            phrase.append(token)
            continue
        flush()
        if re.fullmatch(r'[+-]?\d+(?:[.,]\d+)*',token):
            try:
                token=str(Decimal(token.replace(',','.')).normalize())
            except InvalidOperation:
                pass
        token={'cm':'sentimeter','senti':'sentimeter','balas':'jawab','setengah':'0.5','seperempat':'0.25'}.get(token,token)
        result.append(token)
    flush()
    return ' '.join(result)


class BrowserVoice:
    def __init__(self,database,tools,channels,coordinator,settings:Settings, *,clock=time.time,transport=None):
        self.db,self.tools,self.channels,self.coordinator,self.settings=database,tools,channels,coordinator,settings
        self.clock,self.transport=clock,transport
        self.mint_limit=RateLimit(5,clock=lambda:self.clock())
        self.global_limit=RateLimit(20,clock=lambda:self.clock())
        self.sim_limit=RateLimit(30,clock=lambda:self.clock())
        self._lock=RLock()
        self.last_failure=None

    def _key(self,session_id):
        if not re.fullmatch(r'bv_[0-9a-f]{32}',session_id):
            raise HTTPException(404,'Unknown voice session')
        return 'browser-voice:'+session_id

    def _load(self,session_id,access, *,closed=False):
        session=self.db.get(self._key(session_id))
        if not session or session['expires_at']<=self.clock():
            raise HTTPException(404,'Unknown voice session')
        if session['owner']!=access.owner or session['responder_id']!=access.responder_id:
            raise HTTPException(403,'Voice scope mismatch')
        if session.get('ended') and not closed:
            raise HTTPException(409,'Voice session ended')
        return session

    def _save(self,session):
        self.db.set(self._key(session['id']),session)

    def _new(self,access,offline=False):
        index=self.db.get('browser-voice-index',[])
        retained=[]
        active=[]
        for entry in index:
            if entry['expires_at']<=self.clock():
                with self.db.connect() as connection:
                    connection.execute('DELETE FROM kv WHERE key=?',(self._key(entry['id']),))
                continue
            retained.append(entry)
            state=self.db.get(self._key(entry['id']),{})
            if not state.get('ended'):
                active.append(entry)
        if len(active)>=20 or sum(entry['responder_id']==access.responder_id for entry in active)>=2:
            raise HTTPException(429,'Voice session budget reached')
        current=self.db.get('warden:'+access.responder_id,{})
        trace=current.get('trace_id') if (current.get('mode','live'),current.get('day',0))==(access.mode,access.day) else None
        session={'id':identifier('bv'),'owner':access.owner,'responder_id':access.responder_id,'village_id':access.village_id,
                 'mode':access.mode,'day':access.day,'expires_at':self.clock()+TTL,'offline':offline,
                 'transcripts':{},'tool_results':{},'seq':0,'consumed_seq':0,'allowed_numbers':[],'allowed_urls':[],
                 'coordinator':{'responder_id':access.responder_id,'mode':access.mode,'day':access.day,'trace_id':trace,
                                'stage':'consent','turn':0,'empty':0,'rounds':0,'report':{},'diagnostic':False}}
        retained.append({key:session[key] for key in ('id','responder_id','expires_at')})
        self.db.set('browser-voice-index',retained)
        self._save(session)
        return session

    def mint(self,access):
        with self._lock:
            identity=access.owner if access.field else access.owner+':'+access.responder_id
            if not self.mint_limit.allow(identity) or not self.global_limit.allow('all'):
                raise HTTPException(429,'Voice session request limit')
            if not self.settings.key.startswith('sk-'):
                self.last_failure='missing_key'
                record(self.db,'Warden','realtime_unavailable',trace_id=None,output={'reason':self.last_failure})
                raise ChannelUnavailable('Browser voice unavailable')
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',self.settings.model) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',self.settings.voice):
                raise ChannelUnavailable('Browser voice unavailable')
            session=self._new(access)
        payload={'expires_after':{'anchor':'created_at','seconds':60},'session':{
            'type':'realtime','model':self.settings.model,'instructions':INSTRUCTIONS,'max_output_tokens':256,
            'audio':{'input':{'transcription':{'model':'gpt-4o-mini-transcribe','language':'id'},
                              'turn_detection':{'type':'server_vad','create_response':False,'interrupt_response':False}},'output':{'voice':self.settings.voice}},
            'tools':[{'type':'function','name':name,'description':description,'parameters':{'type':'object','properties':{},'additionalProperties':False}} for name,description in (
                ('get_village_status','Get current scoped engine facts; never compute or invent numbers.'),
                ('record_report','Process latest persisted final USER transcript through the shared report flow. Empty args only.'),
                ('confirm_report','Confirm/correct only a NEW standalone YA/IYA/TIDAK transcript after server readback. Empty args only.'),
                ('end_call','Finish the conversation; say brief thanks. Empty args only.'))]}}
        failure='upstream_unavailable'
        try:
            with httpx.Client(transport=self.transport,timeout=15,follow_redirects=False,trust_env=False) as client:
                response=client.post('https://api.openai.com/v1/realtime/client_secrets',json=payload,
                    headers={'Authorization':'Bearer '+self.settings.key,'OpenAI-Safety-Identifier':hashlib.sha256(('peatforge:'+access.owner+':'+access.responder_id).encode()).hexdigest()})
            if response.status_code!=200 or len(response.content)>65536:
                failure=('upstream_auth' if response.status_code in (401,403) else 'upstream_rate_limit' if response.status_code==429 else
                         'upstream_rejected' if response.status_code<500 else 'upstream_unavailable')
                raise ValueError('Provider unavailable')
            failure='malformed_response'
            body=response.json()
            value,expiry=body.get('value'),body.get('expires_at')
            if not isinstance(value,str) or not value.startswith('ek_') or len(value)>4096 or value==self.settings.key or not isinstance(expiry,int) or isinstance(expiry,bool) or not self.clock()<expiry<=self.clock()+120:
                raise ValueError('Malformed ephemeral credential')
        except (httpx.HTTPError,ValueError,TypeError,AttributeError) as error:
            if isinstance(error,httpx.TimeoutException):
                failure='upstream_timeout'
            elif isinstance(error,httpx.RequestError):
                failure='upstream_connection'
            with self._lock:
                session['ended']=True
                self._save(session)
                self.last_failure=failure
            record(self.db,'Warden','realtime_unavailable',trace_id=session['coordinator']['trace_id'],output={'reason':failure})
            raise ChannelUnavailable('Browser voice unavailable') from None
        self.last_failure=None
        # Ephemeral value is returned once, never added to SQLite/session state/trace.
        return {'session_id':session['id'],'client_secret':value,'expires_at':expiry,'session_expires_at':int(session['expires_at']),
                'model':self.settings.model,'voice':self.settings.voice}

    def _learn(self,session,result):
        # Identifiers, replay offsets and boilerplate assumptions are not spoken
        # scientific facts. Do not accidentally authorize digits from random IDs.
        facts={key:value for key,value in result.items() if key in ('pfvi','wt_cm','days_to_critical','confidence','rain_mm',
               'days_bought','at_least_days','wt_gain_cm','pfvi_reduction','text','facts')}
        session['allowed_numbers']=sorted(set(session['allowed_numbers'])|fact_numbers(facts))[:500]
        session['allowed_urls']=sorted(set(session['allowed_urls'])|set(re.findall(r'https?://[^\s<>]+',json.dumps(result))))[:30]
        if isinstance(result.get('text'),str):
            session['approved_scripts']=list(dict.fromkeys(session.get('approved_scripts',[])+[normalized_script(result['text'])]))[-30:]

    def _transcript(self,session,event_id,role,text, *,store=True):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',event_id) or role not in ('user','assistant') or not text.strip() or len(text)>500:
            raise HTTPException(422,'Invalid final transcript')
        digest=hashlib.sha256(text.encode()).hexdigest()
        previous=session['transcripts'].get(event_id)
        if previous:
            if previous['role']!=role or previous['digest']!=digest:
                raise HTTPException(409,'Transcript event conflict')
            return {'accepted':True,'duplicate':True}
        if len(session['transcripts'])>=100 or session.get('soft_ending') and role=='user':
            raise HTTPException(409,'Voice turn limit reached')
        if role=='assistant':
            urls=set(re.findall(r'https?://[^\s<>]+',text))
            normalized=normalized_script(text)
            expected=session.get('expected_readback')
            approved=normalized==expected if expected and session['coordinator']['stage']=='confirm' else normalized in session.get('approved_scripts',[])
            readback_required=bool(expected and session['coordinator']['stage']=='confirm' and not session.get('readback_validated_seq') and not session.get('soft_ending'))
            if has_numeric_claim(text) and not approved or not urls<=set(session['allowed_urls']) or readback_required and normalized!=expected:
                reason='numeric_guard' if has_numeric_claim(text) or urls else 'readback_guard'
                record(self.db,'Warden','assistant_rejected',trace_id=session['coordinator']['trace_id'],output={'reason':reason})
                return {'accepted':False,'reason':reason}
            if expected and normalized==expected:
                pending=self.db.get('warden:'+session['responder_id'],{}).get('pending')
                if pending!=session.get('readback_pending'):
                    raise HTTPException(409,'Readback report changed')
                session['readback_validated_seq']=session['seq']+1
        session['seq']+=1
        session['transcripts'][event_id]={'role':role,'digest':digest,'seq':session['seq']}
        if role=='user':
            session['latest_user']={'text':text,'event_id':event_id,'seq':session['seq']}
        if store:
            self.channels.record_voice(session['responder_id'],text,direction='inbound' if role=='user' else 'outbound',speech=text if role=='assistant' else None,
                                       trace_id=session['coordinator']['trace_id'],idempotency_key='m_browser_'+hashlib.sha256((session['id']+':'+event_id).encode()).hexdigest())
        self._save(session)
        return {'accepted':True,'duplicate':False}

    def transcript(self,access,session_id,event_id,role,text):
        with self._lock:
            session=self._load(session_id,access)
            return self._transcript(session,event_id,role,text)

    def _process(self,session,text):
        state=session['coordinator']
        before=state['stage']
        # Protect a readback from another channel/alert replacing its pending facts.
        if before=='confirm' and session.get('readback_pending') is not None:
            current=self.db.get('warden:'+session['responder_id'],{})
            if current.get('pending')!=session['readback_pending']:
                external=f"voice:browser_{session['id']}:confirm:{state['turn']}"
                with self.db.connect() as connection:
                    receipt=connection.execute('SELECT id FROM messages WHERE inbound_message_id=?',(external,)).fetchone()
                checkpoint=self.db.get('warden-confirm:'+receipt['id']) if receipt else None
                if not checkpoint or checkpoint.get('observation')!=session['readback_pending']:
                    raise HTTPException(409,'Pending report changed; repeat readback')
                if checkpoint.get('hook_done'):
                    result={'state':'saved','stage':'confirm','text':'Terima kasih. Laporan terkonfirmasi dicatat untuk ditinjau petugas.',
                            'ended':True,'mode':session['mode'],'day':session['day'],'facts':state['report'],
                            'observation_id':checkpoint['saved']['observation_id']}
                    session['soft_ending']=True
                    self._learn(session,result)
                    return result
        response=self.coordinator._turn(state,text,'browser_'+session['id'],state['turn'])
        session['last_reply_id']=response.get('message_id')
        state['turn']+=1
        warden=self.db.get('warden:'+session['responder_id'],{})
        if state['stage']=='confirm' and not response.get('hangup'):
            pending=warden.get('pending')
            if before!='confirm' or pending!=session.get('readback_pending'):
                session['readback_pending']=pending
                session['expected_readback']=normalized_script(response['text'])
                session['readback_validated_seq']=0
        elif state['stage']!='confirm':
            session.pop('expected_readback',None)
            session['readback_validated_seq']=0
        ended=response.get('hangup',False)
        session['soft_ending']=ended
        status='saved' if ended and before=='confirm' and not BYE.search(text) and warden.get('state')=='saved' else 'confirm' if state['stage']=='confirm' and not ended else 'collecting'
        result={'state':status,'stage':state['stage'],'text':response['text'],'ended':ended,'mode':session['mode'],'day':session['day'],'facts':state['report']}
        if status=='saved':
            result['observation_id']=warden.get('observation_id')
        self._learn(session,result)
        return result

    def tool(self,access,session_id,call_id,name,arguments):
        if name not in NAMES or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',call_id) or not isinstance(arguments,dict) or arguments:
            raise HTTPException(422,'Tool not allowed or arguments must be empty')
        with self._lock:
            session=self._load(session_id,access)
            cached=session['tool_results'].get(call_id)
            if cached:
                if cached['name']!=name:
                    raise HTTPException(409,'Tool call identity conflict')
                return cached['result']
            if session.get('soft_ending') or len(session['tool_results'])>=100:
                raise HTTPException(409,'Voice session ending')
            if name=='get_village_status':
                result=self.tools.get_village_status(session['village_id'],session['mode'],session['day']).model_dump()
                result['text']=self.coordinator.opening(session['responder_id']) if session['coordinator']['stage']=='consent' else 'Silakan lanjutkan pengamatan Bapak/Ibu.'
                self._learn(session,result)
            elif name=='end_call':
                session['soft_ending']=True
                result={'ended':True,'text':'Terima kasih sudah mengikuti uji coba Peatforge. Sampai jumpa.'}
                self._learn(session,result)
            else:
                latest=session.get('latest_user')
                if not latest or latest['seq']<=session['consumed_seq']:
                    raise HTTPException(409,'Persist final user transcript before this tool')
                standalone=re.sub(r'[.!?]+$','',latest['text'].strip().lower()).strip()
                if name=='confirm_report' and (session['coordinator']['stage']!='confirm' or standalone not in ('ya','iya','tidak') and not BYE.search(latest['text'])):
                    raise HTTPException(409,'Confirmation requires new standalone user reply after readback')
                if name=='confirm_report' and standalone in ('ya','iya') and (not session.get('readback_validated_seq') or latest['seq']<=session['readback_validated_seq']):
                    raise HTTPException(409,'A validated assistant readback and newer user confirmation are required')
                if name=='record_report' and session['coordinator']['stage']=='confirm' and standalone in ('ya','iya','tidak'):
                    raise HTTPException(409,'Use the explicit confirmation tool after readback')
                result=self._process(session,latest['text'])
                session['consumed_seq']=latest['seq']
            session['tool_results'][call_id]={'name':name,'result':result}
            self._save(session)
            return result

    def simulate(self,access,text,event_id,session_id=None):
        with self._lock:
            if session_id:
                session=self._load(session_id,access)
            else:
                active_key='browser-sim:'+hashlib.sha256(f'{access.owner}:{access.responder_id}:{access.mode}:{access.day}'.encode()).hexdigest()
                existing=self.db.get(active_key)
                try:
                    session=self._load(existing,access) if existing else None
                    if session and session.get('soft_ending'):
                        session=None
                except HTTPException:
                    session=None
                if session is None:
                    session=self._new(access,offline=True)
                    self.db.set(active_key,session['id'])
            call_id='sim_'+hashlib.sha256(event_id.encode()).hexdigest()
            if call_id in session['tool_results']:
                if session['transcripts'].get(event_id,{}).get('digest')!=hashlib.sha256(text.encode()).hexdigest():
                    raise HTTPException(409,'Simulation event conflict')
                result=session['tool_results'][call_id]['result']
            else:
                if not self.sim_limit.allow(access.owner+':'+access.responder_id):
                    raise HTTPException(429,'Simulation turn rate limit')
                ack=self._transcript(session,event_id,'user',text)
                result=self._process(session,text)
                session['consumed_seq']=session['latest_user']['seq']
                session['tool_results'][call_id]={'name':'simulate','result':result}
                self._save(session)
                self._transcript(session,'assistant_'+hashlib.sha256(event_id.encode()).hexdigest(),'assistant',result['text'],store=not session.get('last_reply_id'))
            return {**result,'session_id':session['id'],'items':message_items(self.db,session['responder_id'])}

    def end(self,access,session_id):
        with self._lock:
            session=self._load(session_id,access,closed=True)
            session['ended']=True
            self._save(session)
            return {'ended':True}
