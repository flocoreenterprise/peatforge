from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest
from scripts.public_gateway import Boundary, PublicIngress, authorities


def app():
    api = FastAPI()
    @api.api_route('/{path:path}', methods=['GET','POST','PUT','DELETE','OPTIONS','HEAD'])
    def echo(request: Request):
        return {'path': request.url.path}
    @api.websocket('/{path:path}')
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text('accepted')
        await socket.close()
    return api


def test_public_allowlist_denies_operator_and_spa_fallback_all_methods():
    client = TestClient(Boundary(app(), port=8001, public=True), base_url='http://localhost:8001')
    for path in ('/', '/status', '/api/actions', '/api/voice/session', '/api/observations',
                 '/api/public/messages/extra', '/f/private', '/assets/../api/config'):
        for method in ('GET','POST','PUT','DELETE','OPTIONS','HEAD'):
            assert client.request(method,path).status_code == 404
    for path in ('/lapor', '/api/public/villages', '/api/public/messages', '/assets/index-123.js'):
        assert client.get(path).status_code == 200
    assert client.post('/api/public/sessions').status_code == 200
    assert client.post('/assets/index-123.js').status_code == 404


def test_local_host_origin_and_fetch_metadata_boundary():
    client = TestClient(Boundary(app(),port=8000),base_url='http://localhost:8000')
    assert client.post('/api/actions').status_code == 200
    for headers in ({'Host':'evil.test:8000'}, {'Origin':'https://evil.test'},
                    {'Origin':'null'}, {'Sec-Fetch-Site':'cross-site'}):
        assert client.post('/api/actions',headers=headers).status_code == 403
    assert client.post('/api/actions',headers={'Origin':'http://localhost:8000'}).status_code == 200


def test_public_websocket_exact_path_origin_and_no_query():
    client = TestClient(Boundary(app(),port=8001,public=True),base_url='http://localhost:8001')
    for path in ('/api/voice/stream','/api/public/voice/stream?token=hidden','/api/public/voice/stream/extra'):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('ws://localhost:8001'+path):
                pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('ws://localhost:8001/api/public/voice/stream',headers={'Origin':'https://evil.test'}):
            pass
    with client.websocket_connect('ws://localhost:8001/api/public/voice/stream',headers={'Origin':'http://localhost:8001'}) as socket:
        assert socket.receive_text() == 'accepted'


def test_tunnel_host_is_explicit_and_does_not_widen_local_boundary():
    base='https://example.trycloudflare.com'
    public=TestClient(Boundary(app(),port=8001,public=True,public_base=base),base_url=base)
    assert public.post('/api/public/sessions',headers={'Origin':base}).status_code == 200
    assert public.post('/api/actions').status_code == 404
    local=TestClient(Boundary(app(),port=8000,public_base=base),base_url=base)
    assert local.get('/api/actions').status_code == 403
    for invalid in ('http://example.com','https://user:pass@example.com','https://example.com/private','https://example.com?key=x'):
        with pytest.raises(ValueError):
            authorities(8001,invalid)


def test_phone_ingress_is_explicit_isolated_and_redacted():
    def tagged(label):
        api=FastAPI()
        @api.api_route('/{path:path}',methods=['GET','POST'])
        def http():
            return {'store':label}
        @api.websocket('/{path:path}')
        async def ws(socket:WebSocket):
            await socket.accept()
            await socket.send_json({'store':label,'plain':socket.scope['path']=='/api/voice/stream'})
            await socket.close()
        return api
    gateway=Boundary(PublicIngress(tagged('public'),tagged('protected')),port=8001,public=True,phone=True)
    client=TestClient(gateway,base_url='http://localhost:8001')
    assert client.post('/api/voice/twilio/twiml/start').json()=={'store':'protected'}
    assert client.get('/api/public/messages').json()=={'store':'public'}
    assert client.post('/api/voice/twilio/call').status_code==404
    assert client.get('/api/voice/twilio/twiml/start').status_code==404
    sid='v1.Y2hvc2Vu.1794027600.'+'a'*32+'.2.'+'b'*64
    with client.websocket_connect('ws://localhost:8001/api/voice/stream') as socket:
        assert socket.receive_json()=={'store':'protected','plain':True}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('ws://localhost:8001/api/voice/stream?sid='+sid):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('ws://localhost:8001/api/voice/stream/invalid'):
            pass


def test_public_body_limit_counts_actual_chunked_bytes_before_app():
    import asyncio
    called=[]
    async def target(scope,receive,send):
        called.append(True)
    scope={'type':'http','method':'POST','path':'/api/public/sessions','headers':[(b'host',b'localhost:8001')], 'query_string':b''}
    chunks=iter([{'type':'http.request','body':b'x'*4096,'more_body':True}]*3)
    async def receive():
        return next(chunks)
    sent=[]
    async def send(message):
        sent.append(message)
    asyncio.run(Boundary(target,port=8001,public=True)(scope,receive,send))
    assert not called
    assert sent[0]['status']==413

