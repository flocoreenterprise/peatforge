"""Offline protocol/security tests: no provider or telephony requests."""
import asyncio
import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from engine.ingest import Ingest
from engine.service import EngineService
from .app import create_app
from .phone_realtime import PhoneRealtime, PhoneRelay, RelayError
from .voice import sign_session, twilio_signature

AUDIO=base64.b64encode(b'\xff'*160).decode()
CALL='CA'+'a'*32
STREAM='MZ'+'b'*32


class Socket:
    def __init__(self, frames=(),headers=None):
        self.frames=asyncio.Queue()
        for frame in frames:
            self.frames.put_nowait(json.dumps(frame))
        self.sent=[]
        self.headers=headers or {}
        self.accepted=False
        self.closed=False
    async def receive_text(self):
        return await self.frames.get()
    async def recv(self):
        return await self.receive_text()
    async def send_json(self,body):
        self.sent.append(body)
    async def send(self,body):
        self.sent.append(json.loads(body))
    async def accept(self):
        self.accepted=True
    async def close(self,**kwargs):
        self.closed=True


class PhoneRealtimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.dict('os.environ',{'VOICE_ENGINE':'realtime','OPENAI_API_KEY':'sk-offline-test'}))
        self.app=create_app(db_path=Path(self.temp.name)/'state.sqlite3',public_demo=True,engine=EngineService(Ingest()))
        self.client=self.enterContext(TestClient(self.app,base_url='http://localhost'))
        self.voice=self.app.state.voice
        self.browser=self.app.state.browser_voice
        self.voice.clock=self.browser.clock=lambda:10000
        self.voice.settings.auth_token='a'*32
        self.voice.settings.account_sid='AC'+'c'*32
        self.voice.settings.public_base_url='https://phone.example.test'
        self.sid=sign_session(self.voice.settings,'r1',now=10000)
        from .voice import verify_session
        self.identity=verify_session(self.voice.settings,self.sid,now=10000)
        state=self.voice._session(self.sid,self.identity)
        state['provider_hash']=hashlib.sha256(CALL.encode()).hexdigest()
        self.voice.db.set(self.voice._key(self.sid),state)
        self.phone=PhoneRealtime(self.voice,self.browser)
        self.phone.update_call=AsyncMock(return_value=True)

    def start_frame(self,**changes):
        start={'customParameters':{'sid':self.sid},'callSid':CALL,'streamSid':STREAM,
               'accountSid':self.voice.settings.account_sid,
               'mediaFormat':{'encoding':'audio/x-mulaw','sampleRate':8000,'channels':1}}
        start.update(changes)
        return {'event':'start','start':start}

    def relay(self):
        binding=self.phone.bind(self.start_frame()['start'])
        self.twilio,self.provider=Socket(),Socket()
        return PhoneRelay(self.phone,self.twilio,self.provider,*binding)

    async def complete_audio(self,relay,text='Halo, ini asisten AI Peatforge.'):
        await relay.response()
        await relay.provider_event({'type':'response.created','response':{'id':'resp1'}})
        await relay.provider_event({'type':'response.output_audio.delta','response_id':'resp1','delta':AUDIO})
        self.assertEqual(self.twilio.sent,[])
        await relay.provider_event({'type':'response.output_audio_transcript.done','response_id':'resp1','transcript':text})
        await relay.provider_event({'type':'response.done','response':{'id':'resp1','status':'completed','output':[]}})

    async def test_start_connect_and_unconfigured_nonlooping_fallback(self):
        xml=self.phone.start(self.sid,self.identity)
        self.assertIn('<Connect>',xml)
        self.assertIn('url="wss://phone.example.test/api/voice/stream"',xml)
        self.assertIn('<Parameter name="sid"',xml)
        self.assertIn('<Say',xml)
        self.assertIn('<Redirect',xml)
        self.assertIn('fallback=1',xml)
        self.assertNotIn('Timeout',xml)
        self.assertIsNone(self.phone.start(self.sid,self.identity,fallback=True))
        self.browser.settings.key=''
        self.assertIsNone(self.phone.start(self.sid,self.identity))
        with self.assertRaises(HTTPException):
            self.phone.start('invalid',self.identity,fallback=True)

    async def test_invalid_sid_rejected_before_accept_and_before_provider(self):
        ws=Socket([self.start_frame(customParameters={'sid':'invalid'})])
        self.phone.connector=AsyncMock()
        await self.phone.handle(ws)
        self.assertTrue(ws.accepted)
        self.assertTrue(ws.closed)
        self.phone.connector.assert_not_called()

    async def test_handshake_signature_and_scope_binding(self):
        self.assertTrue(self.phone.handshake_valid(Socket()))
        self.assertFalse(self.phone.handshake_valid(Socket(headers={'x-twilio-signature':'wrong'})))
        signature=twilio_signature(self.voice.settings.auth_token,self.voice.settings.base()+'/api/voice/stream',{})
        self.assertTrue(self.phone.handshake_valid(Socket(headers={'x-twilio-signature':signature})))
        for changes in ({'callSid':'CA'+'d'*32},{'accountSid':'AC'+'d'*32},{'streamSid':'arbitrary'},
                        {'mediaFormat':{'encoding':'audio/pcm','sampleRate':8000,'channels':1}}):
            with self.assertRaises(RelayError):
                self.phone.bind(self.start_frame(**changes)['start'])
        self.phone.bind(self.start_frame()['start'])
        with self.assertRaises(RelayError):
            self.phone.bind(self.start_frame()['start'])

    async def test_both_audio_directions_and_transcripts(self):
        relay=self.relay()
        await self.complete_audio(relay)
        self.assertTrue(relay.audio_sent)
        self.assertEqual(self.twilio.sent[0],{'streamSid':STREAM,'event':'media','media':{'payload':AUDIO}})
        self.assertEqual(self.twilio.sent[1]['event'],'mark')
        self.twilio.frames.put_nowait(json.dumps({'event':'media','streamSid':STREAM,'media':{'payload':AUDIO}}))
        self.twilio.frames.put_nowait(json.dumps({'event':'stop','streamSid':STREAM}))
        await relay.receive_twilio()
        self.assertIn({'type':'input_audio_buffer.append','audio':AUDIO},self.provider.sent)
        with self.voice.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM messages WHERE channel='voice' AND direction='outbound'").fetchone()[0],1)

    async def test_barge_in_clears_playback_and_never_cancels_idle(self):
        relay=self.relay()
        await relay.provider_event({'type':'input_audio_buffer.speech_started'})
        self.assertEqual(self.provider.sent,[])
        await self.complete_audio(relay)
        await relay.provider_event({'type':'input_audio_buffer.speech_started'})
        self.assertEqual(self.twilio.sent[-1]['event'],'clear')
        self.assertFalse(any(x['type']=='response.cancel' for x in self.provider.sent))
        await relay.response()
        await relay.provider_event({'type':'response.created','response':{'id':'resp2'}})
        await relay.provider_event({'type':'input_audio_buffer.speech_started'})
        self.assertEqual(self.provider.sent[-1],{'type':'response.cancel'})

    async def test_tool_roundtrip_scoped_empty_arguments_and_final_user_transcript(self):
        relay=self.relay()
        await relay.provider_event({'type':'conversation.item.input_audio_transcription.completed','item_id':'u1','transcript':'ya'})
        await relay.provider_event({'type':'response.created','response':{'id':'f1'}})
        await relay.provider_event({'type':'response.done','response':{'id':'f1','status':'completed',
            'output':[{'type':'function_call','call_id':'c1','name':'record_report','arguments':'{}'}]}})
        tool_output=[x for x in self.provider.sent if x['type']=='conversation.item.create'][0]['item']
        self.assertEqual(tool_output['type'],'function_call_output')
        result=json.loads(tool_output['output'])
        self.assertEqual(result['stage'],'wt')
        self.assertEqual(self.provider.sent[-1]['type'],'response.create')
        with self.assertRaises(HTTPException):
            relay.tool('evil','record_report',{'responder_id':'r2','wt_cm':999})
        self.assertEqual(self.browser._load(relay.session_id,relay.access)['responder_id'],'r1')

    async def test_invented_number_is_never_played(self):
        relay=self.relay()
        with self.assertRaises(RelayError):
            await self.complete_audio(relay,'Tinggi air 999 sentimeter.')
        self.assertEqual(self.twilio.sent,[])
        self.assertFalse(relay.audio_sent)

    async def test_provider_failure_before_audio_redirects_only_bound_call(self):
        async def fail(_):
            raise RuntimeError('sk-do-not-return-provider-error')
        self.phone.connector=fail
        ws=Socket([self.start_frame()])
        await self.phone.handle(ws)
        self.phone.update_call.assert_awaited_once_with(CALL,{'Url':self.phone.fallback_url(self.sid),'Method':'POST'})
        self.assertTrue(ws.closed)
        self.assertTrue(self.voice.db.get(self.voice._key(self.sid))['realtime_fallback'])
        self.assertNotIn('sk-',str(ws.sent))

    async def test_unconfigured_stream_falls_back_without_upstream(self):
        self.browser.settings.key=''
        self.phone.connector=AsyncMock()
        await self.phone.handle(Socket([self.start_frame()]))
        self.phone.connector.assert_not_called()
        self.phone.update_call.assert_awaited_once()

    async def test_deadline_closes_provider_and_completes_even_before_audio(self):
        provider=Socket()
        self.phone.connector=AsyncMock(return_value=provider)
        self.phone.deadline=0.02
        await self.phone.handle(Socket([self.start_frame()]))
        self.phone.update_call.assert_awaited_once_with(CALL,{'Status':'completed'})
        self.assertTrue(provider.closed)
        update=provider.sent[0]['session']
        self.assertEqual(update['type'],'realtime')
        self.assertEqual(update['audio']['input']['format'],{'type':'audio/pcmu'})
        self.assertEqual(update['audio']['input']['turn_detection']['type'],'semantic_vad')

    async def test_end_call_waits_for_bye_playback_mark(self):
        relay=self.relay()
        await relay.response()
        await relay.provider_event({'type':'response.created','response':{'id':'bye'}})
        await relay.provider_event({'type':'response.done','response':{'id':'bye','status':'completed','output':[
            {'type':'function_call','call_id':'bye1','name':'end_call','arguments':'{}'}]}})
        self.phone.update_call.assert_not_called()
        await self.complete_audio(relay,'Terima kasih sudah mengikuti uji coba Peatforge. Sampai jumpa.')
        self.phone.update_call.assert_not_called()
        self.twilio.frames.put_nowait(json.dumps({'event':'mark','streamSid':STREAM,'mark':{'name':relay.mark}}))
        await relay.receive_twilio()
        self.phone.update_call.assert_awaited_once_with(CALL,{'Status':'completed'})

    async def test_response_correlation_rejects_stale_audio_and_overlapping_created(self):
        relay=self.relay()
        await relay.response()
        await relay.provider_event({'type':'response.created','response':{'id':'current'}})
        for event in (
            {'type':'response.created','response':{'id':'overlap'}},
            {'type':'response.output_audio.delta','response_id':'stale','delta':AUDIO},
            {'type':'response.output_audio_transcript.done','response_id':'stale','transcript':'Halo.'},
            {'type':'response.output_audio.done','response_id':'stale'},
            {'type':'response.done','response':{'id':'stale','status':'completed','output':[]}}):
            with self.assertRaises(RelayError):
                await relay.provider_event(event)
        self.assertEqual(self.twilio.sent,[])
        await relay.provider_event({'type':'response.output_audio_transcript.done','response_id':'current','transcript':'Halo.'})
        # A matching harmless transcript never validates audio from another response.
        with self.assertRaises(RelayError):
            await relay.provider_event({'type':'response.output_audio.delta','response_id':'stale','delta':AUDIO})
        self.assertEqual(relay.audio,[])

    async def test_cancel_ack_blocks_new_response_and_permanently_discards_cancelled_audio(self):
        relay=self.relay()
        await relay.response()
        await relay.provider_event({'type':'response.created','response':{'id':'old'}})
        await relay.provider_event({'type':'input_audio_buffer.speech_started'})
        self.assertTrue(relay.active)
        self.assertTrue(relay.cancel_pending)
        count=len(self.provider.sent)
        await relay.response()
        self.assertEqual(len(self.provider.sent),count)
        await relay.provider_event({'type':'response.done','response':{'id':'old','status':'cancelled','output':[]}})
        self.assertEqual(self.provider.sent[-1]['type'],'response.create')
        self.assertFalse(relay.cancel_pending)
        await relay.provider_event({'type':'response.created','response':{'id':'new'}})
        await relay.provider_event({'type':'response.output_audio.delta','response_id':'old','delta':AUDIO})
        await relay.provider_event({'type':'response.output_audio_transcript.done','response_id':'old','transcript':'999'})
        await relay.provider_event({'type':'response.done','response':{'id':'old','status':'completed','output':[]}})
        self.assertEqual(relay.audio,[])
        self.assertEqual(relay.transcript,'')
        self.assertEqual(relay.response_id,'new')
        self.assertTrue(relay.active)

    async def test_readback_proof_only_after_mark_and_cleared_readback_cannot_confirm(self):
        relay=self.relay()
        # Use the exact same dispatcher/parser as SMS and browser voice.
        for index,text in enumerate(('ya','kering','tidak ada asap','dekat kebun')):
            self.browser.transcript(relay.access,relay.session_id,'u'+str(index),'user',text)
            result=relay.tool('t'+str(index),'record_report',{})
        self.assertEqual(result['stage'],'confirm')
        await relay.response(result['text'])
        await relay.provider_event({'type':'response.created','response':{'id':'readback'}})
        await relay.provider_event({'type':'response.output_audio.delta','response_id':'readback','delta':AUDIO})
        await relay.provider_event({'type':'response.output_audio_transcript.done','response_id':'readback','transcript':result['text']})
        await relay.provider_event({'type':'response.done','response':{'id':'readback','status':'completed','output':[]}})
        self.assertEqual(self.browser._load(relay.session_id,relay.access)['readback_validated_seq'],0)
        self.assertGreater(relay.pending_readback_seq,0)
        await relay.provider_event({'type':'input_audio_buffer.speech_started'})
        self.browser.transcript(relay.access,relay.session_id,'early_yes','user','ya')
        with self.assertRaises(HTTPException):
            relay.tool('early_confirm','confirm_report',{})
        self.assertEqual(relay.pending_readback_seq,0)

    async def test_timeout_remains_terminal_when_rest_completion_fails(self):
        provider=Socket()
        self.phone.connector=AsyncMock(return_value=provider)
        self.phone.update_call=AsyncMock(return_value=False)
        self.phone.deadline=0.02
        await self.phone.handle(Socket([self.start_frame()]))
        self.assertIn('<Hangup',self.phone.start(self.sid,self.identity,fallback=True))
        self.assertTrue(self.voice.db.get(self.voice._key(self.sid))['realtime_terminal'])
        self.assertTrue(provider.closed)

    async def test_fallback_keeps_original_deadline_and_expiry_hangs_up(self):
        self.phone.start(self.sid,self.identity)
        deadline=self.voice.db.get(self.voice._key(self.sid))['realtime_deadline']
        self.assertIsNone(self.phone.start(self.sid,self.identity,fallback=True))
        self.assertEqual(self.voice.db.get(self.voice._key(self.sid))['realtime_deadline'],deadline)
        self.voice.clock=lambda:deadline
        self.assertTrue(self.phone.terminal(self.sid))
        self.assertIn('<Hangup',self.phone.start(self.sid,self.identity,fallback=True))

    async def test_full_fake_websocket_session_greets_relays_and_cleans_up(self):
        provider=Socket()
        twilio=Socket([self.start_frame(),{'event':'media','streamSid':STREAM,'media':{'payload':AUDIO}}])
        async def provider_send(raw):
            event=json.loads(raw)
            provider.sent.append(event)
            replies=[]
            if event['type']=='session.update':
                replies=[{'type':'session.updated'}]
            elif event['type']=='response.create':
                replies=[{'type':'response.created','response':{'id':'greeting'}},
                    {'type':'response.output_audio.delta','response_id':'greeting','item_id':'msg1','delta':AUDIO},
                    {'type':'response.output_audio_transcript.done','response_id':'greeting','item_id':'msg1','transcript':'Halo, ini asisten AI Peatforge.'},
                    {'type':'response.done','response':{'id':'greeting','status':'completed','output':[]}}]
            for reply in replies:
                provider.frames.put_nowait(json.dumps(reply))
        async def twilio_send(event):
            twilio.sent.append(event)
            if event['event']=='mark':
                twilio.frames.put_nowait(json.dumps(event))
                twilio.frames.put_nowait(json.dumps({'event':'stop','streamSid':STREAM}))
        provider.send=provider_send
        twilio.send_json=twilio_send
        self.phone.connector=AsyncMock(return_value=provider)
        await asyncio.wait_for(self.phone.handle(twilio),1)
        self.assertTrue(twilio.accepted and twilio.closed and provider.closed)
        self.assertIn({'type':'input_audio_buffer.append','audio':AUDIO},provider.sent)
        self.assertEqual([event['event'] for event in twilio.sent],['media','mark'])
        self.assertTrue(self.phone.terminal(self.sid))

    async def test_barge_clear_ack_is_ignored_without_restoring_readback_proof(self):
        relay=self.relay()
        await self.complete_audio(relay)
        cleared=relay.mark
        await relay.provider_event({'type':'input_audio_buffer.speech_started'})
        self.assertIsNone(relay.mark)
        self.twilio.frames.put_nowait(json.dumps({'event':'mark','streamSid':STREAM,'mark':{'name':cleared}}))
        self.twilio.frames.put_nowait(json.dumps({'event':'stop','streamSid':STREAM}))
        await relay.receive_twilio()
        self.assertEqual(self.browser._load(relay.session_id,relay.access).get('readback_validated_seq',0),0)
        self.phone.update_call.assert_not_called()

    async def test_watchdog_rejects_arbitrary_callsid_and_arms_once(self):
        for sid,call in (('invalid',CALL),(self.sid,'CA'+'d'*32),(self.sid,'not-call')):
            with self.assertRaises(HTTPException):
                await self.phone.arm_deadline(sid,call)
        await self.phone.arm_deadline(self.sid,CALL)
        first=list(self.phone._watchdogs.values())[0][0]
        deadline=self.voice.db.get(self.voice._key(self.sid))['realtime_deadline']
        await self.phone.arm_deadline(self.sid,CALL)
        self.assertEqual(len(self.phone._watchdogs),1)
        self.assertIs(list(self.phone._watchdogs.values())[0][0],first)
        self.assertEqual(self.voice.db.get(self.voice._key(self.sid))['realtime_deadline'],deadline)
        await self.phone.close()
        self.assertTrue(first.done())
        self.assertEqual(self.phone._watchdogs,{})
        self.assertTrue(self.phone.terminal(self.sid))

    async def test_watchdog_survives_old_twiml_fallback_and_terminates_at_original_deadline(self):
        self.phone.deadline=0.03
        await self.phone.arm_deadline(self.sid,CALL)
        deadline=self.voice.db.get(self.voice._key(self.sid))['realtime_deadline']
        self.assertIsNone(self.phone.start(self.sid,self.identity,fallback=True))
        await self.phone.arm_deadline(self.sid,CALL)
        task=list(self.phone._watchdogs.values())[0][0]
        await asyncio.wait_for(task,1)
        self.assertTrue(self.phone.terminal(self.sid))
        self.phone.update_call.assert_awaited_once_with(CALL,{'Status':'completed'})
        state=self.voice.db.get(self.voice._key(self.sid))
        self.assertEqual(state['realtime_deadline'],deadline)
        self.assertEqual(state['realtime_watchdog_status'],'completed')

    async def test_watchdog_restart_rearms_remaining_time_and_records_rest_failure(self):
        state=self.voice.db.get(self.voice._key(self.sid))
        state['realtime_deadline']=self.voice.clock()-1
        self.voice.db.set(self.voice._key(self.sid),state)
        self.phone.update_call=AsyncMock(return_value=False)
        await self.phone.arm_deadline(self.sid,CALL)
        await asyncio.wait_for(list(self.phone._watchdogs.values())[0][0],1)
        self.assertIn('<Hangup',self.phone.start(self.sid,self.identity,fallback=True))
        self.assertEqual(self.voice.db.get(self.voice._key(self.sid))['realtime_watchdog_status'],'termination_unconfirmed')
        self.phone.update_call.assert_awaited_once_with(CALL,{'Status':'completed'})

    async def test_provider_failure_fallback_does_not_cancel_registered_watchdog(self):
        self.phone.deadline=0.05
        await self.phone.arm_deadline(self.sid,CALL)
        task=list(self.phone._watchdogs.values())[0][0]
        self.phone.connector=AsyncMock(side_effect=RuntimeError('private-provider-failure'))
        ws=Socket([self.start_frame()])
        await self.phone.handle(ws)
        self.assertTrue(ws.closed)
        self.assertFalse(task.done())
        self.assertIsNone(self.phone.start(self.sid,self.identity,fallback=True))
        await asyncio.wait_for(task,1)
        self.assertEqual(self.phone.update_call.await_count,2)
        self.assertEqual(self.phone.update_call.await_args_list[0].args,
            (CALL,{'Url':self.phone.fallback_url(self.sid),'Method':'POST'}))
        self.assertEqual(self.phone.update_call.await_args_list[1].args,(CALL,{'Status':'completed'}))
        self.assertTrue(self.phone.terminal(self.sid))

    async def test_plain_handshake_accepts_only_configured_origin_signature_variants(self):
        base=self.voice.settings.base()+'/api/voice/stream'
        for url in (base,base+'/',base.replace('https://','wss://'),base.replace('https://','wss://')+'/'):
            signature=twilio_signature(self.voice.settings.auth_token,url,{})
            self.assertTrue(self.phone.handshake_valid(Socket(headers={'x-twilio-signature':signature})),url)
        foreign=twilio_signature(self.voice.settings.auth_token,'wss://other.example/api/voice/stream',{})
        self.assertFalse(self.phone.handshake_valid(Socket(headers={'x-twilio-signature':foreign})))

    async def test_plain_stream_bad_signature_or_missing_sid_never_connect_provider(self):
        self.phone.connector=AsyncMock()
        bad=Socket([self.start_frame()],headers={'x-twilio-signature':'wrong'})
        await self.phone.handle(bad)
        self.assertFalse(bad.accepted)
        missing=Socket([self.start_frame(customParameters={})])
        await self.phone.handle(missing)
        self.assertTrue(missing.accepted and missing.closed)
        self.phone.connector.assert_not_called()
        diagnostics=self.voice.db.get('phone-realtime-diagnostics')
        self.assertEqual(diagnostics['stream_handshake_seen'],2)
        self.assertEqual(diagnostics['stream_handshake_denied'],1)
        self.assertEqual(diagnostics['last_reason'],'start_invalid')
        self.assertNotIn(self.sid,json.dumps(diagnostics))
        self.assertNotIn(CALL,json.dumps(diagnostics))

    async def test_unauthenticated_plain_stream_is_bounded_by_first_frame_deadline(self):
        self.phone.handshake_timeout=0.01
        self.phone.connector=AsyncMock()
        ws=Socket()
        await asyncio.wait_for(self.phone.handle(ws),1)
        self.assertTrue(ws.accepted and ws.closed)
        self.phone.connector.assert_not_called()
        self.assertEqual(self.phone._connections,0)
        self.assertEqual(self.voice.db.get('phone-realtime-diagnostics')['last_reason'],'start_timeout')
