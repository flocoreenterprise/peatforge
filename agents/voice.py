"""Twilio TwiML test calls. No SDK, OpenAI, recordings, or implicit live mode."""
import base64
from dataclasses import dataclass,field
from datetime import datetime,timezone
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from threading import RLock
import time
from urllib.parse import parse_qs,urlencode,urlsplit
import xml.etree.ElementTree as ET
import httpx
from fastapi import HTTPException
from .channels import ChannelAdapter,ChannelUnavailable,Inbound
from .contacts import normalize_phone
from .db import Database,identifier
from .events import record
from .scout import reserve_call,decision,WIB
from .tools import ToolService
from .warden import Warden,parse_report
from .persona import BYE,WATER_QUESTION,SMOKE_QUESTION,PLACE_QUESTION,greeting,category_facts

SID_TTL=15*60
# Strong session capability, intentionally incompatible with the rehearsal plan:
# v1.<base64url responder ID>.<UTC epoch seconds>.<128-bit hex nonce>.<purpose 0 normal|1 diag|2 UAT>.<full SHA256 HMAC>
# MAC key = configured TWILIO_AUTH_TOKEN; message = "twilio-session:" + first five fields.
# Valid for 15 minutes (+30s clock skew). Never print/store in trace or return in operator JSON.



class _PrivateTwilioLog(logging.Filter):
    def filter(self,entry):
        # httpx INFO includes full URLs; the status query contains a private To.
        return 'api.twilio.com' not in entry.getMessage()


logging.getLogger('httpx').addFilter(_PrivateTwilioLog())


def redact_transcript(text):
    text=re.sub(r'https?://\S+|/f/[A-Za-z0-9_-]{43}|\bv1\.[A-Za-z0-9_.-]+','[url]',str(text),flags=re.I)
    def hide(match):
        return '[phone]' if len(re.sub(r'\D','',match.group()))>=8 else match.group()
    text=re.sub(r'\+?\d(?:[\d(). -]*\d)?',hide,text)
    return ''.join(char for char in text if char>=' ' or char in '\n\t')[:500]


def reserve_uat_pair(database,responder_id,level,now,diagnostic):
    """One explicit CLI-only diagnostic→normal pair, max two attempts/WIB day.

    No ordinary counter reset. Only the second matching normal UAT attempt may
    skip the daily cap; quiet hours and cooldown always apply.
    """
    key='call-policy:'+responder_id
    pair_key='uat-pair:'+responder_id
    today=now.astimezone(WIB).date().isoformat()
    with database.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        def read(name):
            row=connection.execute('SELECT value FROM kv WHERE key=?',(name,)).fetchone()
            return json.loads(row['value']) if row else {}
        state,pair=read(key),read(pair_key)
        count=state.get('count',0) if state.get('date')==today else 0
        paired=pair.get('count',0) if pair.get('date')==today else 0
        if paired>=2:
            return False,'uat_pair_complete'
        if paired==0:
            if not diagnostic:
                return False,'uat_pair_requires_diagnostic'
            allowed,reason=decision('voice',level,now,calls_today=count,last_call=state.get('last_call'),risk_override=True)
        else:
            if diagnostic or count!=1 or pair.get('phase')!='diagnostic':
                return False,'uat_pair_sequence'
            allowed,reason=decision('voice',level,now,calls_today=0,last_call=state.get('last_call'),risk_override=True)
        if not allowed:
            return False,reason
        updates={key:{'date':today,'count':count+1,'last_call':now.timestamp()},
                 pair_key:{'date':today,'count':paired+1,'phase':'diagnostic' if diagnostic else 'complete'}}
        for name,value in updates.items():
            connection.execute('INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(name,json.dumps(value)))
    return True,'allowed_uat_pair'


@dataclass(repr=False)
class VoiceSettings:
    account_sid:str=field(default='',repr=False)
    auth_token:str=field(default='',repr=False)
    from_number:str=field(default='',repr=False)
    public_base_url:str=''
    uat_phone:str=field(default='',repr=False)

    @classmethod
    def from_environment(cls):
        return cls(**{name:os.environ.get(env,'') for name,env in {
            'account_sid':'TWILIO_ACCOUNT_SID','auth_token':'TWILIO_AUTH_TOKEN','from_number':'TWILIO_FROM_NUMBER',
            'public_base_url':'PUBLIC_BASE_URL','uat_phone':'UAT_PHONE_NUMBER'}.items()})

    def base(self):
        parts=urlsplit(self.public_base_url)
        if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
            raise ChannelUnavailable('Voice configuration unavailable')
        return self.public_base_url.rstrip('/')

    def validate(self):
        self.base()
        if not re.fullmatch(r'AC[0-9a-fA-F]{32}',self.account_sid) or not re.fullmatch(r'[0-9a-fA-F]{32}',self.auth_token):
            raise ChannelUnavailable('Voice configuration unavailable')
        try:
            if normalize_phone(self.from_number)!=self.from_number or normalize_phone(self.uat_phone)!=self.uat_phone:
                raise ValueError('Invalid voice contact')
        except ValueError:
            raise ChannelUnavailable('Voice configuration unavailable') from None


def sign_session(settings:VoiceSettings,responder_id:str, *,diagnostic=False,uat=False,now=None):
    if not re.fullmatch(r'[0-9a-fA-F]{32}',settings.auth_token) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',responder_id):
        raise ChannelUnavailable('Voice session unavailable')
    encoded=base64.urlsafe_b64encode(responder_id.encode()).decode().rstrip('=')
    purpose='1' if diagnostic else '2' if uat else '0'
    payload=f'v1.{encoded}.{int(time.time() if now is None else now)}.{secrets.token_hex(16)}.{purpose}'
    mac=hmac.new(settings.auth_token.encode(),('twilio-session:'+payload).encode(),hashlib.sha256).hexdigest()
    return payload+'.'+mac


def verify_session(settings:VoiceSettings,sid:str, *,now=None):
    if not re.fullmatch(r'[0-9a-fA-F]{32}',settings.auth_token) or len(sid)>300:
        return None
    parts=sid.split('.')
    if len(parts)!=6 or parts[0]!='v1' or not re.fullmatch(r'[A-Za-z0-9_-]{1,110}',parts[1]) or not re.fullmatch(r'[0-9]{1,12}',parts[2]) or not re.fullmatch(r'[0-9a-f]{32}',parts[3]) or parts[4] not in ('0','1','2') or not re.fullmatch(r'[0-9a-f]{64}',parts[5]):
        return None
    age=(time.time() if now is None else now)-int(parts[2])
    if age < -30 or age>SID_TTL:
        return None
    mac=hmac.new(settings.auth_token.encode(),('twilio-session:'+'.'.join(parts[:5])).encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac,parts[5]):
        return None
    try:
        responder_id=base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)).decode()
    except (ValueError,UnicodeError):
        return None
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',responder_id):
        return None
    return {'responder_id':responder_id,'diagnostic':parts[4]=='1','uat':parts[4]=='2','issued_at':int(parts[2])}


def twilio_signature(auth_token:str,url:str,params:dict[str,list[str]]):
    payload=url+''.join(key+value for key in sorted(params) for value in sorted(set(params[key])))
    return base64.b64encode(hmac.new(auth_token.encode(),payload.encode(),hashlib.sha1).digest()).decode()


def speech_text(text:str):
    return text.replace('Balas YA','Jawab ya').replace('TIDAK','tidak').replace('YA','ya').replace(' cm',' sentimeter').replace('K4','kanal empat')


def twiml(text:str,url:str|None=None, *,pause=False,diagnostic=False,hangup=False):
    root=ET.Element('Response')
    if pause:
        ET.SubElement(root,'Pause',length='1')
    parent=root
    if not hangup:
        parent=ET.SubElement(root,'Gather',input='speech',language='en-US' if diagnostic else 'id-ID',speechTimeout='auto',timeout='5',
                             actionOnEmptyResult='true',action=url,method='POST',hints='air,asap,kanal,ya,tidak')
    ET.SubElement(parent,'Say',voice='alice' if diagnostic else 'Google.id-ID-Standard-A',language='en-US' if diagnostic else 'id-ID').text=speech_text(text)
    if hangup:
        ET.SubElement(root,'Hangup')
    else:
        ET.SubElement(root,'Redirect',method='POST').text=url
    return ET.tostring(root,encoding='unicode',xml_declaration=False)


class VoiceService:
    def __init__(self,database:Database,tools:ToolService,channels:ChannelAdapter,warden:Warden,settings:VoiceSettings, *,clock=time.time,transport=None):
        self.db,self.tools,self.channels,self.warden,self.settings=database,tools,channels,warden,settings
        self.clock,self.transport=clock,transport
        self._lock=RLock()

    def mode(self):
        if self.channels.settings.public_demo or self.channels.settings.force_mock:
            return 'mock'
        return 'live' if self.ready() else 'unavailable'

    def ready(self):
        try:
            if self.channels.settings.public_demo or self.channels.settings.force_mock:
                return False
            self.settings.validate()
            contacts=self.channels.contacts()
            responder=contacts.find(self.settings.uat_phone)
            return responder is not None and contacts.phone_for(responder)==self.settings.uat_phone
        except (ChannelUnavailable,ValueError):
            return False

    def _key(self,sid):
        return 'voice-session:'+hashlib.sha256(sid.encode()).hexdigest()

    def _context(self,responder_id):
        with self.db.connect() as connection:
            row=connection.execute('SELECT village_id FROM responders WHERE id=?',(responder_id,)).fetchone()
        if not row:
            raise KeyError('Unknown responder')
        state=self.db.get('warden:'+responder_id,{})
        mode,day=state.get('mode','live'),state.get('day',0)
        return self.tools.get_village_status(row['village_id'],mode,day),state

    def realtime_account_block(self):
        """Read-only capability check before reserving budget or creating a call.

        Twilio's current trial strips Stream; successful call creation is not
        evidence that Media Streams is available. Never disclose provider data.
        """
        try:
            with httpx.Client(transport=self.transport,timeout=8,trust_env=False,
                              follow_redirects=False,
                              auth=(self.settings.account_sid,self.settings.auth_token)) as client:
                response=client.get('https://api.twilio.com/2010-04-01/Accounts/'+self.settings.account_sid+'.json')
                if response.status_code!=200:
                    return 'twilio_account_capability_unavailable'
                account_type=response.json().get('type')
                if account_type=='Trial':
                    return 'twilio_trial_media_streams_unavailable'
                if account_type!='Full':
                    return 'twilio_account_capability_unavailable'
        except Exception:
            return 'twilio_account_capability_unavailable'
        return None

    def call(self,responder_id,diagnostic=False, *,uat=False,cli_uat_pair=False,cli_realtime_uat=False,cli_retry_rejected=False,server_limit_only=False,cli_stream_repair=False):
        if cli_stream_repair and (not cli_realtime_uat or not server_limit_only or cli_retry_rejected or cli_uat_pair or diagnostic):
            raise ChannelUnavailable('Choose the dedicated attended stream repair flow')
        if (cli_retry_rejected or server_limit_only or cli_stream_repair) and not cli_realtime_uat:
            raise ChannelUnavailable('Dedicated attended Realtime UAT required')
        if cli_realtime_uat:
            if diagnostic or cli_uat_pair or os.environ.get('VOICE_ENGINE')!='realtime' or not os.environ.get('OPENAI_API_KEY','').strip():
                raise ChannelUnavailable('Realtime UAT requires the configured real engine and its single dedicated flow')
            uat=True
        if cli_uat_pair and not diagnostic:
            uat=True
        if diagnostic and uat:
            raise ValueError('Choose one test flow')
        status,context=self._context(responder_id)
        mock=self.channels.settings.public_demo or self.channels.settings.force_mock
        # Replay is conversation context only. A real phone attempt always applies
        # quiet/risk policy to CURRENT LIVE facts; only explicit chosen UAT/diag
        # may bypass the risk threshold, never quiet hours or the call budget.
        policy_status=status if mock else self.tools.get_village_status(status.village_id,'live',0)
        phone=None
        if not mock:
            self.settings.validate()
            phone=self.channels.contacts().phone_for(responder_id)
            if not hmac.compare_digest(phone.encode(),self.settings.uat_phone.encode()):
                raise ChannelUnavailable('Only the consented chosen test recipient is enabled')
        if (diagnostic or uat) and mock:
            # Diagnostic risk bypass is reserved for the actual chosen consenting UAT contact.
            raise ChannelUnavailable('Diagnostic requires the chosen protected test recipient')
        if (not mock and not diagnostic and os.environ.get('VOICE_ENGINE','twiml').strip().lower() in ('auto','realtime')
                and os.environ.get('OPENAI_API_KEY','').strip()):
            blocked=self.realtime_account_block()
            if blocked:
                return {'call_id':None,'status':'suppressed','reason':blocked}
        now=datetime.fromtimestamp(self.clock(),timezone.utc)
        no_limits=(not mock and os.environ.get('DEMO_NO_CALL_LIMITS')=='1'
                   and os.environ.get('DEMO_NO_CALL_LIMITS_DATE')==now.astimezone(WIB).date().isoformat())
        if no_limits:
            # Explicit attended demo override expires at WIB midnight. Consent,
            # chosen recipient, authentication and public delivery gates still apply.
            allowed,reason=decision('voice',policy_status.level,now,hour_override=12,
                                    risk_override=bool(diagnostic or uat))
        elif cli_realtime_uat and not mock:
            from .uat_realtime import reserve_realtime_uat
            allowed,reason=reserve_realtime_uat(self.db,responder_id,policy_status.level,now,retry_rejected=cli_retry_rejected,stream_repair=cli_stream_repair)
        else:
            allowed,reason=(reserve_uat_pair(self.db,responder_id,policy_status.level,now,diagnostic) if cli_uat_pair and not mock else
                            reserve_call(self.db,responder_id,policy_status.level,now,risk_override=bool((diagnostic or uat) and not mock)))
        call_id=identifier('vc')
        trace=context.get('trace_id') or identifier('t')
        if not allowed:
            record(self.db,'Scout','call_suppressed',trace_id=trace,input={'responder_id':responder_id,'level':policy_status.level,'mode':policy_status.mode,'day':policy_status.day},output={'reason':reason,'status':'suppressed'})
            return {'call_id':None,'status':'suppressed','reason':reason}
        if mock:
            self.channels.record_voice(responder_id,'Simulasi permintaan panggilan uji coba Peatforge. Tidak ada telepon nyata.',trace_id=trace)
            record(self.db,'Warden','call_mocked',trace_id=trace,input={'responder_id':responder_id,'mode':status.mode,'day':status.day},output={'call_id':call_id,'status':'mock'})
            return {'call_id':call_id,'status':'mock'}
        if not diagnostic:
            self.warden.reset_for_alert(responder_id,status.mode,status.day,trace)
        sid=sign_session(self.settings,responder_id,diagnostic=diagnostic,uat=uat,now=self.clock())
        session={'responder_id':responder_id,'diagnostic':diagnostic,'uat':uat,'call_id':call_id,'trace_id':trace,'mode':status.mode,'day':status.day,
                 'stage':'diagnostic' if diagnostic else 'consent','turn':0,'empty':0,'rounds':0,'report':{},'responses':{},'status':'dispatching'}
        self.db.set(self._key(sid),session)
        form={'To':phone,'From':self.settings.from_number,'Url':self.settings.base()+'/api/voice/twilio/twiml/start?'+urlencode({'sid':sid}),
              'StatusCallback':self.settings.base()+'/api/voice/twilio/status?'+urlencode({'sid':sid})}
        if not server_limit_only and not diagnostic and os.environ.get('VOICE_ENGINE','twiml').strip().lower() in ('auto','realtime') and os.environ.get('OPENAI_API_KEY','').strip():
            # Carrier duration cap survives relay disconnects and the TwiML fallback.
            form['TimeLimit']='180'
        outcome='unknown'
        try:
            with httpx.Client(transport=self.transport,timeout=15,follow_redirects=False,trust_env=False,
                              auth=(self.settings.account_sid,self.settings.auth_token)) as client:
                response=client.post('https://api.twilio.com/2010-04-01/Accounts/'+self.settings.account_sid+'/Calls.json',data=form)
                if response.status_code>=400:
                    session['http_status']=response.status_code
                    outcome='failed' if response.status_code<500 else 'unknown'
                    try:
                        payload=response.json()
                        code=payload.get('code')
                        if isinstance(code,int) and not isinstance(code,bool) and 0<=code<1000000:
                            session['error_code']=code
                        message=str(payload.get('message','')).lower()
                        if 'parameter' in message:
                            session['error_class']='parameter_rejected'
                    except (ValueError,AttributeError):
                        pass
                else:
                    provider_id=response.json().get('sid','')
                    if re.fullmatch(r'CA[0-9a-fA-F]{32}',provider_id):
                        session['provider_hash']=hashlib.sha256(provider_id.encode()).hexdigest()
                        outcome='queued'
        except (httpx.HTTPError,ValueError,TypeError):
            outcome='unknown'
        # Merge a callback that may have arrived before the REST response.
        with self._lock:
            current=self.db.get(self._key(sid),session)
            current.update(status=outcome)
            if session.get('provider_hash'):
                current['provider_hash']=session['provider_hash']
            for name in ('error_code','error_class','http_status'):
                if name in session:
                    current[name]=session[name]
            self.db.set(self._key(sid),current)
        record(self.db,'Warden','call_requested',trace_id=trace,input={'responder_id':responder_id,'mode':status.mode,'day':status.day,'policy_level':policy_status.level,'diagnostic':diagnostic,'uat':uat},
               output={'call_id':call_id,'status':outcome})
        return {'call_id':call_id,'status':outcome}

    def status_report(self,responder_id):
        """Explicit read-only CLI status, restricted to the configured consenting UAT phone."""
        with self.db.connect() as connection:
            if not connection.execute('SELECT 1 FROM responders WHERE id=?',(responder_id,)).fetchone():
                raise KeyError('Unknown responder')
            rows=connection.execute("SELECT direction,body FROM messages WHERE responder_id=? AND channel='voice' ORDER BY created_at DESC,rowid DESC LIMIT 30",(responder_id,)).fetchall()
            saved=connection.execute("SELECT value FROM kv WHERE key LIKE 'voice-session:%'").fetchall()
        transcript=[{'direction':row['direction'],'text':redact_transcript(row['body'])} for row in reversed(rows)]
        attempts=[]
        for row in saved:
            state=json.loads(row['value'])
            if state.get('responder_id')==responder_id:
                attempts.append({'call_id':state['call_id'],'status':state.get('provider_status',state.get('status','unknown')),
                                 'error_code':state.get('error_code'),'http_status':state.get('http_status'),'error_class':state.get('error_class')})
        result={'mode':self.mode(),'calls':[],'attempts':attempts[-5:],'transcript':transcript,'error_code':None}
        if self.channels.settings.public_demo or self.channels.settings.force_mock:
            return result
        self.settings.validate()
        phone=self.channels.contacts().phone_for(responder_id)
        if not hmac.compare_digest(phone.encode(),self.settings.uat_phone.encode()):
            raise ChannelUnavailable('Only chosen UAT status is enabled')
        try:
            with httpx.Client(transport=self.transport,timeout=10,follow_redirects=False,trust_env=False,
                              auth=(self.settings.account_sid,self.settings.auth_token)) as client:
                response=client.get('https://api.twilio.com/2010-04-01/Accounts/'+self.settings.account_sid+'/Calls.json',params={'To':phone,'PageSize':5})
            body=response.json()
            if response.status_code!=200:
                code=body.get('code') if isinstance(body,dict) else None
                result['error_code']=code if isinstance(code,int) and not isinstance(code,bool) and 0<code<1000000 else 'provider_unavailable'
                return result
            for call in body.get('calls',[])[:5]:
                if call.get('to')!=phone or not re.fullmatch(r'CA[0-9a-fA-F]{32}',call.get('sid','')):
                    continue
                if call.get('status') not in ('queued','initiated','ringing','in-progress','completed','busy','failed','no-answer','canceled'):
                    continue
                code=call.get('error_code')
                result['calls'].append({'call_sid':call['sid'],'status':call['status'],
                    'error_code':code if isinstance(code,int) and not isinstance(code,bool) and 0<code<1000000 else None})
        except (httpx.HTTPError,ValueError,TypeError,AttributeError):
            result['error_code']='provider_unavailable'
        return result

    def authenticate(self,path,raw_query,params,signature):
        try:
            query=parse_qs(raw_query.decode('ascii'),keep_blank_values=True,max_num_fields=10)
            sid=query.get('sid',[])
            if len(sid)!=1:
                raise ValueError('Invalid session')
            identity=verify_session(self.settings,sid[0],now=self.clock())
            if not identity:
                raise ValueError('Invalid session')
            public_url=self.settings.base()+path+('?' + raw_query.decode('ascii') if raw_query else '')
            if signature is not None:
                expected=twilio_signature(self.settings.auth_token,public_url,params)
                if not hmac.compare_digest(expected.encode(),signature.encode()):
                    raise ValueError('Invalid signature')
            if params.get('AccountSid') and params['AccountSid']!=[self.settings.account_sid]:
                raise ValueError('Account scope mismatch')
            existing=self.db.get(self._key(sid[0]),{})
            if existing.get('provider_hash'):
                provider=params.get('CallSid',[''])[0]
                if hashlib.sha256(provider.encode()).hexdigest()!=existing['provider_hash']:
                    raise ValueError('Call scope mismatch')
            with self.db.connect() as connection:
                if not connection.execute('SELECT 1 FROM responders WHERE id=?',(identity['responder_id'],)).fetchone():
                    raise ValueError('Unknown session')
            with self._lock:
                current=self.db.get(self._key(sid[0]))
                if current:
                    current['provider_callback_received']=True
                    self.db.set(self._key(sid[0]),current)
            return sid[0],identity,query
        except (ValueError,UnicodeError,ChannelUnavailable):
            raise HTTPException(403,'Voice webhook authentication required') from None

    def _session(self,sid,identity):
        session=self.db.get(self._key(sid))
        if session is None:
            # Strong SID is verifiable in any process; persisted context is preferred.
            status,context=self._context(identity['responder_id'])
            session={'responder_id':identity['responder_id'],'diagnostic':identity['diagnostic'],'uat':identity.get('uat',False),'call_id':identifier('vc'),
                     'trace_id':context.get('trace_id') or identifier('t'),'mode':status.mode,'day':status.day,'stage':'diagnostic' if identity['diagnostic'] else 'consent',
                     'turn':0,'empty':0,'rounds':0,'report':{},'responses':{}}
        return session

    def _render(self,sid,session,response,pause=False):
        url=self.settings.base()+'/api/voice/twilio/twiml/gather?'+urlencode({'sid':sid,'turn':response.get('next_turn',session['turn'])})
        return twiml(response['text'],url,pause=pause,diagnostic=session['diagnostic'],hangup=response.get('hangup',False))

    def start(self,sid,identity):
        with self._lock:
            session=self._session(sid,identity)
            text=('This is a consenting Peatforge test call. Please say a short sentence.' if session['diagnostic'] else
                  self.opening(session['responder_id']))
            if session.get('last_response'):
                return self._render(sid,session,session['last_response'],pause=True)
            self.channels.record_voice(session['responder_id'],text,speech=text,trace_id=session['trace_id'],idempotency_key='m_voice_start_'+hashlib.sha256(sid.encode()).hexdigest())
            self.db.set(self._key(sid),session)
            return self._render(sid,session,{'text':text},pause=True)

    def gather(self,sid,identity,query,params):
        with self._lock:
            session=self._session(sid,identity)
            self.db.set('scout:active-context',{'mode':session['mode'],'day':session['day'],'updated_at':self.clock()})
            try:
                values=query.get('turn',[])
                if len(values)!=1 or not re.fullmatch(r'[0-9]{1,2}',values[0]):
                    raise ValueError('Invalid turn')
                turn=int(values[0])
            except ValueError:
                raise HTTPException(403,'Invalid voice turn') from None
            if str(turn) in session['responses']:
                return self._render(sid,session,session['responses'][str(turn)])
            if session.get('ended'):
                return self._render(sid,session,session['last_response'])
            if turn!=session['turn']:
                raise HTTPException(409,'Voice turn is stale')
            speech=params.get('SpeechResult',[''])[0].strip()
            if len(speech)>500:
                raise HTTPException(422,'Speech too long')
            digest=hashlib.sha256(sid.encode()).hexdigest()
            if speech:
                self.channels.record_voice(session['responder_id'],speech,direction='inbound',trace_id=session['trace_id'],idempotency_key=f'm_voice_raw_{digest}_{turn}')
            response=self._turn(session,speech,digest,turn)
            response['next_turn']=turn+1
            session['turn']=turn+1
            session['responses'][str(turn)]=response
            session['last_response']=response
            session['ended']=response.get('hangup',False)
            self.db.set(self._key(sid),session)
            if response.get('message_id'):
                with self.db.connect() as connection:
                    connection.execute('UPDATE messages SET body=?,speech=? WHERE id=?',(response['text'],speech_text(response['text']),response['message_id']))
            else:
                self.channels.record_voice(session['responder_id'],response['text'],speech=speech_text(response['text']),trace_id=session['trace_id'],idempotency_key=f'm_voice_out_{digest}_{turn}')
            return self._render(sid,session,response)

    def opening(self,responder_id):
        with self.db.connect() as connection:
            row=connection.execute('SELECT name,village_id FROM responders WHERE id=?',(responder_id,)).fetchone()
        return greeting(row['name'] if row else None,row['village_id'] if row else None)

    def _turn(self,session,speech,digest,turn):
        if BYE.search(speech):
            return {'text':'Terima kasih sudah mengikuti uji coba Peatforge. Sampai jumpa.','hangup':True}
        if not speech:
            session['empty']+=1
            return {'text':'Terima kasih. Panggilan uji coba diakhiri.' if session['empty']>=3 else 'Suara belum terdengar. Mohon jawab pertanyaan tadi.', 'hangup':session['empty']>=3}
        session['empty']=0
        if session['diagnostic']:
            session['rounds']+=1
            return {'text':'Thank you. The Peatforge test call is complete.' if session['rounds']>=5 else 'Thank you. Please say another short sentence.', 'hangup':session['rounds']>=5}
        if turn>=20:
            return {'text':'Terima kasih. Silakan melanjutkan laporan lewat pesan.','hangup':True}
        stage=session['stage']
        report=session['report']
        if stage=='confirm':
            result=self.channels.inbound(Inbound(responder_id=session['responder_id'],channel='voice',text=speech,mode=session['mode'],day=session['day']),external_id=f'voice:{digest}:confirm:{turn}')
            outgoing=[item for item in result.get('items',[]) if item['direction']=='outbound' and item['channel']=='voice']
            reply=outgoing[-1] if outgoing else None
            if result.get('state')=='saved':
                return {'text':reply['body'] if reply else 'Terima kasih. Laporan dicatat untuk ditinjau petugas.','hangup':True,'message_id':reply['id'] if reply else None}
            if result.get('state')=='idle':
                session.update(stage='wt',report={})
                return {'text':'Baik, laporan belum disimpan. '+WATER_QUESTION}
            return {'text':reply['body'] if reply else 'Apakah pembacaan ulang benar? Jawab ya untuk menyimpan, atau tidak untuk koreksi.','message_id':reply['id'] if reply else None}
        if stage=='consent':
            answer=speech.strip().lower().strip('.!?')
            if answer in ('tidak','nggak','gak','tidak bersedia','tidak mau'):
                return {'text':'Baik, tidak apa-apa. Terima kasih atas waktunya.','hangup':True}
            if answer in ('ya','iya','boleh','silakan','silahkan','ada','oke','ok','mau','bersedia'):
                session['stage']='wt'
                return {'text':WATER_QUESTION}
            # An unsolicited observation is itself willingness to report; never ask a number.
            facts,clarify=parse_report(speech if re.search(r'\bair\b',speech,re.I) else 'AIR '+speech)
            if not facts or clarify:
                return {'text':'Mohon perjelas pengamatan airnya. '+WATER_QUESTION} if clarify else {'text':'Boleh saya mencatat pengamatan air, asap, dan tempat di sekitar Bapak/Ibu?'}
            session['stage']='wt'
            stage='wt'
        if stage=='wt':
            facts,clarify=parse_report(speech if re.search(r'\bair\b',speech,re.I) else 'AIR '+speech)
            facts=facts or {}
            facts.update(category_facts(speech,water=True))
            if clarify or not (facts.get('water_category') or facts.get('wt_cm') is not None):
                return {'text':'Saya belum menangkap keadaan airnya. '+WATER_QUESTION}
            report.update({key:value for key,value in facts.items() if key in ('wt_cm','water_category')})
            report['reported_words']={'water':speech[:200]}
            session['stage']='smoke'
            return {'text':SMOKE_QUESTION}
        if stage=='smoke':
            facts=category_facts(speech,smoke=True)
            value=speech.strip().lower().strip('.!?')
            if not facts and value in ('ya','iya','ada'):
                return {'text':'Apakah terlihat asap, atau hanya tercium bau?'}
            if not facts:
                parsed,_=parse_report(speech)
                if parsed and parsed.get('smoke') is not None:
                    facts={'smoke':parsed['smoke'],'smoke_category':'terlihat' if parsed['smoke'] else 'tidak'}
            if not facts:
                return {'text':'Saya belum menangkap keadaan asapnya. '+SMOKE_QUESTION}
            report.update(facts)
            report.setdefault('reported_words',{})['smoke']=speech[:200]
            session['stage']='landmark'
            safety='Jauhi lokasi, jangan padamkan sendiri. ' if facts.get('fire') else ''
            return {'text':safety+PLACE_QUESTION}
        place=speech.strip()
        if not place or len(place)>150:
            return {'text':PLACE_QUESTION}
        report['place_text']=place
        report.setdefault('reported_words',{})['place']=place
        result=self.channels.inbound(Inbound(responder_id=session['responder_id'],channel='voice',text='Pengamatan warga',report=report,mode=session['mode'],day=session['day']),external_id=f'voice:{digest}:report:{turn}')
        session['stage']='confirm'
        outgoing=[item for item in result.get('items',[]) if item['direction']=='outbound' and item['channel']=='voice']
        return {'text':speech_text(outgoing[-1]['body']) if outgoing else 'Apakah laporan sudah sesuai? Jawab ya atau tidak.','message_id':outgoing[-1]['id'] if outgoing else None}

    def status(self,sid,identity,params):
        value=params.get('CallStatus',[''])[0]
        if value not in ('queued','initiated','ringing','in-progress','completed','busy','failed','no-answer','canceled'):
            raise HTTPException(422,'Invalid call status')
        duration=params.get('CallDuration',[''])[0]
        seconds=int(duration) if re.fullmatch(r'[0-9]{1,5}',duration) and int(duration)<=86400 else None
        with self._lock:
            session=self._session(sid,identity)
            provider=params.get('CallSid',[''])[0]
            if session.get('provider_hash') and (not provider or hashlib.sha256(provider.encode()).hexdigest()!=session['provider_hash']):
                raise HTTPException(403,'Call scope mismatch')
            session['provider_status']=value
            error=params.get('ErrorCode',[''])[0]
            if re.fullmatch(r'[0-9]{1,6}',error) and int(error)>0:
                session['error_code']=int(error)
            self.db.set(self._key(sid),session)
            record(self.db,'Warden','call_status',trace_id=session['trace_id'],input={'call_id':session['call_id'],'responder_id':session['responder_id']},
                   output={'status':value,'duration_seconds':seconds,'error_code':session.get('error_code')},event_id='e_voice_status_'+hashlib.sha256(sid.encode()).hexdigest()+'_'+value+'_'+str(seconds))
        return {'accepted':True}
