from contextlib import asynccontextmanager
from fastapi import FastAPI,Request
from fastapi.testclient import TestClient
from scripts.serve_uat import UatApplication,CALLBACKS

def test_exact_callback_routing_and_lifetimes():
    events=[]
    def make(label):
        @asynccontextmanager
        async def lifespan(app):
            events.append(label+':start')
            yield
            events.append(label+':stop')
        app=FastAPI(lifespan=lifespan)
        @app.api_route('/{path:path}',methods=['GET','POST'])
        async def route(request:Request):
            return {'store':label,'path':request.scope['path'],'query':request.scope['query_string'].decode()}
        return app
    with TestClient(UatApplication(make('public'),make('protected'))) as client:
        assert events==['public:start','protected:start']
        for path in CALLBACKS:
            data=client.post(path+'?sid=test-signature').json()
            assert data=={'store':'protected','path':path,'query':'sid=test-signature'}
            assert client.get(path).json()['store']=='public'
        for path in ('/api/voice/twilio/call','/api/responders','/api/voice/session','/api/voice/twilio/status/extra'):
            assert client.post(path).json()['store']=='public'
    assert events==['public:start','protected:start','protected:stop','public:stop']

def test_factory_rejects_shared_store(monkeypatch):
    from types import SimpleNamespace
    import agents.app
    monkeypatch.setattr(agents.app,'create_app',lambda **kwargs:SimpleNamespace(state=SimpleNamespace(database_path='same')))
    from scripts.serve_uat import application
    import pytest
    with pytest.raises(ValueError,match='isolated'):
        application()
