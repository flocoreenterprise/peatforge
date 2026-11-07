"""Server-owned Twilio/OpenAI relay. Never exposes provider credentials or raw errors.

Integration: after normal VoiceService.authenticate(), use start() or old start().
Mount handle(websocket) on /api/voice/stream. Twilio forbids URL queries.
The first start frame carries the full HMAC capability in a custom Parameter;
no provider connection or audio is allowed until HMAC and call binding pass.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import httpx
from fastapi import HTTPException
from .realtime import Access, INSTRUCTIONS, NAMES
from .voice import verify_session, twilio_signature

MAX_FRAME = 65536
MAX_CHUNKS = 4096
MAX_AUDIO = 480000  # at most one minute of PCMU pending validation


class RelayError(Exception):
    """Deliberately carries no upstream data."""


_provider_log=logging.getLogger('peatforge.phone_provider')
_provider_log.disabled=True


async def provider_connection(settings):
    from websockets.asyncio.client import connect
    return await connect('wss://api.openai.com/v1/realtime?'+urlencode({'model':settings.model}),
                         additional_headers={'Authorization':'Bearer '+settings.key},
                         open_timeout=8, close_timeout=2, max_size=MAX_FRAME,
                         max_queue=16, proxy=None, logger=_provider_log)


class PhoneRealtime:
    def __init__(self, voice, browser, *, connector=provider_connection, deadline=180):
        self.voice, self.browser, self.connector = voice, browser, connector
        self.deadline = min(180, max(0.01, deadline))
        self._watchdogs={}
        self._closing=False
        self._connections=0
        self.handshake_timeout=5

    def enabled(self):
        return (os.environ.get('VOICE_ENGINE','twiml').strip().lower() in ('auto','realtime')
                and self.browser.settings.key.startswith('sk-'))

    def start(self, sid, identity, *, fallback=False):
        # Even the fallback flag cannot bypass the full signed capability.
        valid = verify_session(self.voice.settings, sid, now=self.voice.clock())
        if not valid or valid != identity:
            raise HTTPException(403, 'Voice authentication required')
        state = self.voice.db.get(self.voice._key(sid), {})
        if self.terminal(sid):
            return '<Response><Hangup /></Response>'
        if fallback or state.get('realtime_fallback') or identity['diagnostic'] or not self.enabled():
            return None
        if state.get('responder_id') != identity['responder_id']:
            return None
        with self.voice._lock:
            state=self.voice.db.get(self.voice._key(sid),{})
            if not state.get('realtime_deadline'):
                state['realtime_deadline']=self.voice.clock()+self.deadline
                self.voice.db.set(self.voice._key(sid),state)
        root = ET.Element('Response')
        stream = ET.SubElement(ET.SubElement(root,'Connect'),'Stream',
                               url=self.voice.settings.base().replace('https://','wss://',1)+'/api/voice/stream')
        ET.SubElement(stream,'Parameter',name='sid',value=sid)
        # Audible recovery also covers stream setup failures before a start frame.
        ET.SubElement(root,'Say',language='id-ID',voice='Google.id-ID-Standard-A').text="Maaf, sambungan suara terputus. Saya lanjutkan dengan suara cadangan."
        ET.SubElement(root,'Redirect',method='POST').text=self.fallback_url(sid)
        return ET.tostring(root,encoding='unicode')

    async def arm_deadline(self, sid, call):
        """Arm once after authenticated start; raw CallSid stays only in task memory.

        The persisted provider hash is required even if a webhook's HMAC is valid.
        Fallback callbacks and process restarts retain the original wall deadline.
        """
        identity=verify_session(self.voice.settings,sid,now=self.voice.clock())
        if not identity or not isinstance(call,str) or not re.fullmatch(r'CA[0-9a-fA-F]{32}',call):
            raise HTTPException(403,'Voice scope mismatch')
        key=hashlib.sha256(sid.encode()).hexdigest()
        with self.voice._lock:
            state=self.voice.db.get(self.voice._key(sid),{})
            digest=hashlib.sha256(call.encode()).hexdigest()
            if (state.get('responder_id')!=identity['responder_id'] or not state.get('provider_hash')
                    or not hmac.compare_digest(state['provider_hash'],digest)):
                raise HTTPException(403,'Voice scope mismatch')
            if identity['diagnostic'] or not self.enabled() and not state.get('realtime_deadline'):
                return False
            if self._closing:
                raise HTTPException(503,'Voice unavailable')
            if key in self._watchdogs:
                return True
            if len(self._watchdogs)>=32:
                raise HTTPException(429,'Voice session limit')
            state.setdefault('realtime_deadline',self.voice.clock()+self.deadline)
            self.voice.db.set(self.voice._key(sid),state)
            remaining=max(0,state['realtime_deadline']-self.voice.clock())
        task=asyncio.create_task(self._watch_deadline(sid,call,remaining))
        self._watchdogs[key]=(task,sid,call)
        task.add_done_callback(lambda finished:self._watchdog_done(key,finished))
        return True

    def _watchdog_done(self, key, task):
        self._watchdogs.pop(key,None)
        # Retrieve exceptions without ever passing their text to a logger.
        if not task.cancelled():
            task.exception()

    async def _watch_deadline(self, sid, call, remaining):
        await asyncio.sleep(remaining)
        self.finish(sid)
        completed=await self.update_call(call,{'Status':'completed'})
        with self.voice._lock:
            state=self.voice.db.get(self.voice._key(sid),{})
            state['realtime_watchdog_status']='completed' if completed else 'termination_unconfirmed'
            self.voice.db.set(self.voice._key(sid),state)

    async def close(self):
        """Cancel owned timers and attempt termination of their bound calls on shutdown."""
        self._closing=True
        entries=list(self._watchdogs.values())
        for task,_,_ in entries:
            task.cancel()
        await asyncio.gather(*(entry[0] for entry in entries),return_exceptions=True)
        self._watchdogs.clear()
        for _,sid,_ in entries:
            self.finish(sid)
        await asyncio.gather(*(self.update_call(call,{'Status':'completed'})
                               for _,_,call in entries),return_exceptions=True)

    def terminal(self, sid):
        if not verify_session(self.voice.settings,sid,now=self.voice.clock()):
            raise HTTPException(403,'Voice authentication required')
        with self.voice._lock:
            state=self.voice.db.get(self.voice._key(sid),{})
            expired=bool(state.get('realtime_deadline') and state['realtime_deadline']<=self.voice.clock())
            if expired:
                state['realtime_terminal']=True
                state['ended']=True
                self.voice.db.set(self.voice._key(sid),state)
            return bool(expired or state.get('realtime_terminal') or state.get('ended'))

    def finish(self, sid):
        with self.voice._lock:
            state=self.voice.db.get(self.voice._key(sid),{})
            state['realtime_terminal']=True
            state['ended']=True
            self.voice.db.set(self.voice._key(sid),state)

    def fallback_url(self, sid):
        return self.voice.settings.base()+'/api/voice/twilio/twiml/start?'+urlencode({'sid':sid,'fallback':'1'})

    def diagnostic(self, stage, reason=None):
        """Only bounded categories/counts; never headers, URLs, capabilities or IDs."""
        with self.voice._lock:
            state=self.voice.db.get('phone-realtime-diagnostics',{})
            state[stage]=min(1000000,state.get(stage,0)+1)
            state['last_stage']=stage
            if reason:
                state['last_reason']=reason
            self.voice.db.set('phone-realtime-diagnostics',state)

    def handshake_valid(self, websocket):
        try:
            if not re.fullmatch(r'[0-9a-fA-F]{32}',self.voice.settings.auth_token):
                return False
            base=self.voice.settings.base()+'/api/voice/stream'
            signature=websocket.headers.get('x-twilio-signature')
            if signature is None:
                # Socket is untrusted until the bounded first start frame verifies.
                return True
            if not isinstance(signature,str) or len(signature)>200:
                return False
            # Twilio documents a WSS trailing slash signature discrepancy. These
            # are the only accepted variants, all on the configured external origin.
            urls=(base,base+'/',base.replace('https://','wss://',1),
                  base.replace('https://','wss://',1)+'/')
            return any(hmac.compare_digest(signature,twilio_signature(self.voice.settings.auth_token,url,{}))
                       for url in urls)
        except Exception:
            return False

    def bind(self, start):
        sid = start.get('customParameters',{}).get('sid','')
        if not isinstance(sid,str):
            raise RelayError()
        identity = verify_session(self.voice.settings,sid,now=self.voice.clock())
        if not identity or identity['diagnostic']:
            raise RelayError()
        with self.voice._lock:
            known=self.voice.db.get(self.voice._key(sid),{})
            if known.get('responder_id')==identity['responder_id']:
                known['stream_handshake_seen']=True
                self.voice.db.set(self.voice._key(sid),known)
        call = start.get('callSid','')
        stream = start.get('streamSid','')
        if (not isinstance(call,str) or not re.fullmatch(r'CA[0-9a-fA-F]{32}',call)
                or not isinstance(stream,str) or not re.fullmatch(r'MZ[0-9a-fA-F]{32}',stream)
                or start.get('accountSid') != self.voice.settings.account_sid):
            raise RelayError()
        fmt = start.get('mediaFormat',{})
        if fmt != {'encoding':'audio/x-mulaw','sampleRate':8000,'channels':1}:
            raise RelayError()
        with self.voice._lock:
            state = self.voice.db.get(self.voice._key(sid),{})
            digest = hashlib.sha256(call.encode()).hexdigest()
            if (state.get('responder_id') != identity['responder_id'] or not state.get('provider_hash')
                    or not hmac.compare_digest(state['provider_hash'],digest) or state.get('realtime_claimed')):
                raise RelayError()
            if self.terminal(sid):
                raise RelayError()
            state.setdefault('realtime_deadline',self.voice.clock()+self.deadline)
            state['stream_handshake_seen']=True
            state['realtime_claimed'] = True
            self.voice.db.set(self.voice._key(sid),state)
        status,_ = self.voice._context(identity['responder_id'])
        access = Access('phone:'+hashlib.sha256(sid.encode()).hexdigest(),identity['responder_id'],
                        status.village_id,state['mode'],state['day'])
        with self.browser._lock:
            session = self.browser._new(access)
        return sid,call,stream,access,session['id']

    async def update_call(self, call, form):
        # call can only originate from bind(), whose hash is server-persisted.
        try:
            async with httpx.AsyncClient(transport=self.voice.transport,timeout=8,trust_env=False,
                    follow_redirects=False,auth=(self.voice.settings.account_sid,self.voice.settings.auth_token)) as client:
                result = await client.post('https://api.twilio.com/2010-04-01/Accounts/'+
                    self.voice.settings.account_sid+'/Calls/'+call+'.json',data=form)
                return result.status_code < 400
        except Exception:
            return False

    async def handle(self, websocket):
        self.diagnostic('stream_handshake_seen')
        if self._closing or self._connections>=8 or not self.handshake_valid(websocket):
            self.diagnostic('stream_handshake_denied','signature_or_capacity')
            await websocket.close(code=1008)
            return
        self._connections+=1
        try:
            await websocket.accept()
        except Exception:
            self._connections-=1
            self.diagnostic('stream_handshake_denied','accept_failed')
            return
        provider = relay = binding = None
        phase='start_invalid'
        try:
            async with asyncio.timeout(self.deadline):
                async with asyncio.timeout(self.handshake_timeout):
                    for _ in range(3):
                        frame = await websocket.receive_text()
                        if len(frame)>4096:
                            raise RelayError()
                        event = json.loads(frame)
                        if event.get('event')=='start':
                            binding = self.bind(event.get('start',{}))
                            self.diagnostic('stream_start_bound')
                            break
                        if event.get('event')!='connected':
                            raise RelayError()
                if binding is None:
                    raise RelayError()
                sid,call,stream,access,session_id = binding
                if not self.enabled():
                    raise RelayError()
                phase='provider_unavailable'
                provider = await self.connector(self.browser.settings)
                self.diagnostic('stream_provider_connected')
                phase='relay_error'
                relay = PhoneRelay(self,websocket,provider,*binding)
                state=self.voice.db.get(self.voice._key(sid),{})
                remaining=max(0,state['realtime_deadline']-self.voice.clock())
                async with asyncio.timeout(remaining):
                    await relay.run()
                if relay.audio_sent or relay.ending:
                    self.finish(sid)
        except TimeoutError:
            self.diagnostic('stream_failed','deadline' if binding else 'start_timeout')
            if binding:
                self.finish(binding[0])
            if provider is not None:
                try:
                    await provider.close()
                except Exception:
                    pass
            if binding:
                await self.update_call(binding[1],{'Status':'completed'})
        except (Exception, asyncio.CancelledError):
            self.diagnostic('stream_failed',phase)
            # No exception text enters logs, frames, traces, or REST data.
            if binding:
                sid,call,_,_,_ = binding
                if relay is None or not relay.audio_sent:
                    with self.voice._lock:
                        state=self.voice.db.get(self.voice._key(sid),{})
                        state['realtime_fallback']=True
                        self.voice.db.set(self.voice._key(sid),state)
                    await self.update_call(call,{'Url':self.fallback_url(sid),'Method':'POST'})
                else:
                    self.finish(sid)
                    await self.update_call(call,{'Status':'completed'})
        finally:
            self._connections-=1
            if provider is not None:
                try:
                    await provider.close()
                except Exception:
                    pass
            if binding:
                try:
                    self.browser.end(binding[3],binding[4])
                except Exception:
                    pass
            try:
                await websocket.close()
            except Exception:
                pass


class PhoneRelay:
    def __init__(self, service, twilio, provider, sid, call, stream, access, session_id):
        self.service,self.twilio,self.provider = service,twilio,provider
        self.sid,self.call,self.stream,self.access,self.session_id = sid,call,stream,access,session_id
        self.active = self.speaking = self.audio_sent = self.ending = False
        self.audio=[]
        self.audio_size=0
        self.transcript=''
        self.response_id=None
        self.response_count=0
        self.cancelled=set()
        self.seen_responses=set()
        self.mark=None
        self.cleared_marks=set()
        self.mark_at=0
        self.cancel_pending=False
        self.queued_response=None
        self.pending_readback_seq=0
        self.mark_deadline=0
        self.inbound_frames=0
        self.provider_frames=0
        self.audio_item=None

    async def send(self, body):
        await self.provider.send(json.dumps(body))

    async def tw(self, body):
        await self.twilio.send_json({'streamSid':self.stream,**body})

    def tool(self, call_id, name, arguments):
        return self.service.browser.tool(self.access,self.session_id,call_id,name,arguments)

    async def response(self, text=None):
        if self.cancel_pending:
            self.queued_response=(text,)
            return
        if self.active:
            return
        self.active=True
        self.response_id=None
        body={'type':'response.create'}
        if text:
            body['response']={'instructions':'Ucapkan hanya teks berikut persis, tanpa tambahan: '+text,'tool_choice':'none'}
        await self.send(body)

    async def run(self):
        tools=[{'type':'function','name':name,'parameters':{'type':'object','properties':{},'additionalProperties':False}} for name in NAMES]
        await self.send({'type':'session.update','session':{'type':'realtime','model':self.service.browser.settings.model,
            'instructions':INSTRUCTIONS,'output_modalities':['audio'],'max_output_tokens':256,
            'audio':{'input':{'format':{'type':'audio/pcmu'},'transcription':{'model':'gpt-4o-mini-transcribe','language':'id'},
                'turn_detection':{'type':'semantic_vad','create_response':False,'interrupt_response':False}},
                'output':{'format':{'type':'audio/pcmu'},'voice':self.service.browser.settings.voice}},'tools':tools}})
        tasks=[asyncio.create_task(self.receive_twilio()),asyncio.create_task(self.receive_provider()),
               asyncio.create_task(self.watch_mark())]
        try:
            done,_ = await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)

    async def watch_mark(self):
        while True:
            await asyncio.sleep(0.1)
            if self.mark and asyncio.get_running_loop().time()>self.mark_deadline:
                if self.ending:
                    self.service.finish(self.sid)
                    await self.service.update_call(self.call,{'Status':'completed'})
                    return
                # Do not accept confirmation without proof that a readback played.
                raise RelayError()

    async def receive_twilio(self):
        while True:
            raw=await self.twilio.receive_text()
            if len(raw)>MAX_FRAME:
                raise RelayError()
            event=json.loads(raw)
            if event.get('streamSid') != self.stream:
                raise RelayError()
            kind=event.get('event')
            if kind=='stop':
                return
            if kind=='media':
                self.inbound_frames+=1
                if self.inbound_frames>15000:
                    raise RelayError()
                payload=event.get('media',{}).get('payload')
                self.check_audio(payload,8192)
                await self.send({'type':'input_audio_buffer.append','audio':payload})
            elif kind=='mark' and event.get('mark',{}).get('name') in self.cleared_marks:
                # Twilio acknowledges marks removed by clear; these are NOT playback proof.
                continue
            elif kind=='mark' and self.mark and event.get('mark',{}).get('name')==self.mark:
                self.mark=None
                self.speaking=False
                if self.pending_readback_seq:
                    with self.service.browser._lock:
                        state=self.service.browser._load(self.session_id,self.access)
                        state['readback_validated_seq']=self.pending_readback_seq
                        self.service.browser._save(state)
                    self.pending_readback_seq=0
                if self.ending:
                    self.service.finish(self.sid)
                    await self.service.update_call(self.call,{'Status':'completed'})
                    return
            else:
                raise RelayError()

    @staticmethod
    def check_audio(payload, limit):
        if not isinstance(payload,str) or len(payload)>limit:
            raise RelayError()
        try:
            if not base64.b64decode(payload,validate=True):
                raise RelayError()
        except (ValueError,TypeError):
            raise RelayError() from None

    async def receive_provider(self):
        while True:
            raw=await self.provider.recv()
            if len(raw)>MAX_FRAME:
                raise RelayError()
            event=json.loads(raw)
            self.provider_frames+=1
            if self.provider_frames>50000:
                raise RelayError()
            await self.provider_event(event)

    async def provider_event(self,event):
        kind=event.get('type')
        if kind=='error':
            raise RelayError()
        if kind=='session.updated':
            result=self.tool('phone_opening','get_village_status',{})
            await self.response(result['text'])
        elif kind=='response.created':
            response_id=event.get('response',{}).get('id')
            if (not self.active or self.response_id is not None or not isinstance(response_id,str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',response_id) or response_id in self.seen_responses):
                raise RelayError()
            self.response_id=response_id
            self.seen_responses.add(response_id)
            self.response_count+=1
            if self.response_count>100:
                raise RelayError()
            if self.cancel_pending:
                self.cancelled.add(response_id)
            self.audio=[]
            self.audio_size=0
            self.transcript=''
            self.audio_item=None
        elif kind=='input_audio_buffer.speech_started':
            if self.speaking:
                if self.mark:
                    self.cleared_marks.add(self.mark)
                await self.tw({'event':'clear'})
                self.speaking=False
                self.mark=None
                self.pending_readback_seq=0
                # A cleared readback did not finish playing, so it cannot authorize YA.
                with self.service.browser._lock:
                    state=self.service.browser._load(self.session_id,self.access)
                    state['readback_validated_seq']=0
                    self.service.browser._save(state)
            if self.active and not self.cancel_pending:
                if self.response_id:
                    self.cancelled.add(self.response_id)
                self.cancel_pending=True
                await self.send({'type':'response.cancel'})
                self.audio=[]
                self.audio_size=0
        elif kind=='conversation.item.input_audio_transcription.completed':
            if self.ending:
                return
            # Wait for a playback acknowledgement: prevents early YA from confirming.
            if self.speaking:
                raise RelayError()
            ack=self.service.browser.transcript(self.access,self.session_id,
                event.get('item_id',''), 'user',event.get('transcript',''))
            if not ack.get('accepted'):
                raise RelayError()
            await self.response()
        elif kind in ('response.output_audio.delta','response.output_audio.done',
                      'response.output_audio_transcript.delta','response.output_audio_transcript.done'):
            response_id=event.get('response_id')
            if response_id in self.cancelled:
                return
            if not self.active or not response_id or response_id!=self.response_id:
                raise RelayError()
            if self.cancel_pending:
                return
            item=event.get('item_id')
            if item is not None:
                if not isinstance(item,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',item):
                    raise RelayError()
                if self.audio_item is None:
                    self.audio_item=item
                elif self.audio_item!=item:
                    raise RelayError()
            if event.get('content_index',0)!=0:
                raise RelayError()
            if kind=='response.output_audio.delta':
                payload=event.get('delta')
                self.check_audio(payload,MAX_FRAME)
                self.audio_size+=len(base64.b64decode(payload))
                if self.audio_size>MAX_AUDIO or len(self.audio)>=MAX_CHUNKS:
                    raise RelayError()
                self.audio.append(payload)
            elif kind=='response.output_audio_transcript.done':
                self.transcript=event.get('transcript','')
        elif kind=='response.done':
            await self.response_done(event.get('response',{}))

    async def response_done(self,response):
        response_id=response.get('id')
        if response_id in self.cancelled and response_id!=self.response_id:
            return
        if not self.active or not response_id or response_id!=self.response_id:
            raise RelayError()
        if self.cancel_pending or response.get('status')=='cancelled':
            self.cancelled.add(response_id)
            self.cancel_pending=False
            self.active=False
            self.response_id=None
            self.audio=[]
            self.audio_size=0
            if self.queued_response is not None:
                queued=self.queued_response
                self.queued_response=None
                await self.response(*queued)
            return
        self.active=False
        self.response_id=None
        if response.get('status')!='completed':
            raise RelayError()
        outputs=response.get('output',[])
        calls=[item for item in outputs if item.get('type')=='function_call']
        if calls and self.audio:
            raise RelayError()
        if calls:
            if len(calls)>4:
                raise RelayError()
            reply=None
            for item in calls:
                arguments=json.loads(item.get('arguments','{}'))
                result=self.tool(item.get('call_id',''),item.get('name',''),arguments)
                self.ending=self.ending or bool(result.get('ended'))
                reply=result.get('text',reply)
                await self.send({'type':'conversation.item.create','item':{'type':'function_call_output',
                    'call_id':item['call_id'],'output':json.dumps(result,ensure_ascii=False)}})
            await self.response(reply)
            return
        if not self.audio:
            raise RelayError()
        # Final response output is authoritative if transcript.done did not arrive.
        texts=[part.get('transcript','') for item in outputs for part in item.get('content',[]) if part.get('type') in ('audio','output_audio')]
        text=' '.join(texts).strip() or self.transcript
        ack=self.service.browser.transcript(self.access,self.session_id,
            'phone_assistant_'+str(self.response_count),'assistant',text)
        if not ack.get('accepted'):
            raise RelayError()
        with self.service.browser._lock:
            state=self.service.browser._load(self.session_id,self.access)
            self.pending_readback_seq=state.get('readback_validated_seq',0)
            if self.pending_readback_seq:
                state['readback_validated_seq']=0
                self.service.browser._save(state)
        playback_seconds=self.audio_size/8000
        for payload in self.audio:
            await self.tw({'event':'media','media':{'payload':payload}})
            self.audio_sent=True
        self.audio=[]
        self.audio_size=0
        self.speaking=True
        self.mark='phone_played_'+str(self.response_count)
        self.mark_at=asyncio.get_running_loop().time()
        self.mark_deadline=self.mark_at+playback_seconds+3
        await self.tw({'event':'mark','mark':{'name':self.mark}})
