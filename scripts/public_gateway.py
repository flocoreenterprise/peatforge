"""Separate local operator and allowlisted public ingress; never expose operator APIs."""
from urllib.parse import urlsplit
import asyncio
import re
from starlette.responses import JSONResponse


PUBLIC_HTTP = {
    ('GET', '/lapor'), ('GET', '/api/public/villages'),
    ('POST', '/api/public/sessions'), ('GET', '/api/public/messages'),
    ('POST', '/api/public/messages'), ('POST', '/api/public/voice/session'),
    ('POST', '/api/public/voice/end'),
}
PUBLIC_WS = '/api/public/voice/stream'
PHONE_HTTP=frozenset(('/api/voice/twilio/twiml/start','/api/voice/twilio/twiml/gather','/api/voice/twilio/status'))
PHONE_PATH=re.compile(r'/api/voice/stream/v1\.[A-Za-z0-9_-]{1,107}\.[0-9]{10}\.[0-9a-f]{32}\.[012]\.[0-9a-f]{64}')


def authorities(port, public_base=''):
    values = {f'127.0.0.1:{port}', f'localhost:{port}'}
    if public_base:
        parsed = urlsplit(public_base)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('Public base must be an HTTPS origin')
        values.add(parsed.netloc.lower())
    return values


async def reject(scope, receive, send, status=403):
    if scope['type'] == 'websocket':
        await send({'type': 'websocket.close', 'code': 1008})
    else:
        await JSONResponse({'error': {'code': 'forbidden', 'message': 'Akses tidak tersedia'}},
                           status_code=status, headers={'Cache-Control': 'no-store'})(scope, receive, send)


class Boundary:
    def __init__(self, app, *, port, public=False, public_base='', phone=False):
        self.app, self.public = app, public
        self.phone=phone
        self.hosts = authorities(port, public_base if public else '')
        self.origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
        if public and public_base:
            self.origins.add(public_base.rstrip('/').lower())

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        original_path=scope.get('path','')
        if original_path.startswith('/api/voice/stream/'):
            original_send=send
            async def redacted_send(message):
                scope['path']='/api/voice/stream/[redacted]'
                scope['raw_path']=b'/api/voice/stream/[redacted]'
                scope['query_string']=b''
                if 'sid' in scope.get('path_params',{}):
                    scope['path_params']['sid']='[redacted]'
                await original_send(message)
            send=redacted_send
        raw_headers = scope.get('headers', [])
        headers = {key.lower(): value.decode('latin1') for key, value in raw_headers}
        if sum(key.lower() == b'host' for key, _ in raw_headers) != 1 or headers.get(b'host', '').lower() not in self.hosts:
            return await reject(scope, receive, send)
        origin = headers.get(b'origin')
        if origin is not None and origin.lower() not in self.origins:
            return await reject(scope, receive, send)
        mutation = scope['type'] == 'websocket' or scope.get('method') not in ('GET', 'HEAD', 'OPTIONS')
        if mutation and headers.get(b'sec-fetch-site') == 'cross-site':
            return await reject(scope, receive, send)
        if self.public:
            path = scope.get('path', '')
            if scope['type'] == 'websocket':
                allowed = path == PUBLIC_WS and not scope.get('query_string')
                allowed = allowed or bool(self.phone and PHONE_PATH.fullmatch(path) and not scope.get('query_string'))
            else:
                allowed = (scope.get('method'), path) in PUBLIC_HTTP
                allowed = allowed or self.phone and scope.get('method')=='POST' and path in PHONE_HTTP
                if scope.get('method') == 'GET' and re.fullmatch(r'/assets/[A-Za-z0-9_.-]+\.(?:js|mjs|css|woff2?|png|svg|webp|ico)', path):
                    allowed = True
            if not allowed:
                return await reject(scope, receive, send, 404)
            if scope['type']=='http' and scope.get('method')=='POST' and path.startswith('/api/public/'):
                body=bytearray()
                try:
                    async with asyncio.timeout(10):
                        while True:
                            message=await receive()
                            if message['type']=='http.disconnect':
                                return
                            chunk=message.get('body',b'')
                            if len(body)+len(chunk)>8192:
                                return await reject(scope,receive,send,413)
                            body.extend(chunk)
                            if not message.get('more_body',False):
                                break
                except TimeoutError:
                    return await reject(scope,receive,send,408)
                consumed=False
                original_receive=receive
                async def bounded_receive():
                    nonlocal consumed
                    if not consumed:
                        consumed=True
                        return {'type':'http.request','body':bytes(body),'more_body':False}
                    return await original_receive()
                receive=bounded_receive
        await self.app(scope, receive, send)


class PublicIngress:
    def __init__(self,public,protected=None):
        self.public,self.protected=public,protected

    async def __call__(self,scope,receive,send):
        path=scope.get('path','')
        phone=(scope['type']=='websocket' and PHONE_PATH.fullmatch(path) or
               scope['type']=='http' and scope.get('method')=='POST' and path in PHONE_HTTP)
        target=self.protected if phone and self.protected is not None else self.public
        await target(scope,receive,send)


async def serve_pair(app, *, port=8000, public_port=8001, public_base='', protected=None):
    """One lifespan/database/budget registry, two loopback listeners; no tunnel here."""
    import asyncio
    import uvicorn
    from contextlib import AsyncExitStack
    options = dict(host='127.0.0.1', access_log=False, proxy_headers=False, lifespan='off',
                   ws_max_size=65536, ws_max_queue=4)
    local = uvicorn.Server(uvicorn.Config(Boundary(app, port=port), port=port, **options))
    public = uvicorn.Server(uvicorn.Config(Boundary(PublicIngress(app,protected), port=public_port, public=True, public_base=public_base,phone=protected is not None),
                                           port=public_port, **options))
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app.router.lifespan_context(app))
        if protected is not None:
            if app.state.database_path==protected.state.database_path:
                raise ValueError('Phone UAT and public reports require isolated stores')
            await stack.enter_async_context(protected.router.lifespan_context(protected))
        tasks = [asyncio.create_task(local.serve()), asyncio.create_task(public.serve())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            local.should_exit = public.should_exit = True
            await asyncio.gather(*tasks)
