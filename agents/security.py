"""Security headers and scope redaction before any ASGI access log response."""
from starlette.datastructures import MutableHeaders

CSP=("default-src 'self'; script-src 'self'; worker-src 'self' blob:; "
     "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
     "font-src 'self' https://fonts.gstatic.com data:; "
     "img-src 'self' data: blob: https://server.arcgisonline.com https://*.arcgisonline.com https://tile.openstreetmap.org https://*.tile.openstreetmap.org; "
     "connect-src 'self' https://server.arcgisonline.com https://*.arcgisonline.com https://tile.openstreetmap.org https://*.tile.openstreetmap.org; "
     "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")


class SecurityHeaders:
    def __init__(self,app):
        self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        original_path=scope.get('path','')
        private=original_path.startswith('/api/') or original_path.startswith('/f/')
        def redact():
            scope['query_string']=b''
            if original_path.startswith('/api/field/'):
                scope['path']='/api/field/[redacted]'
                scope['raw_path']=b'/api/field/[redacted]'
            elif original_path.startswith('/f/'):
                scope['path']='/f/[redacted]'
                scope['raw_path']=b'/f/[redacted]'
            if 'token' in scope.get('path_params',{}):
                scope['path_params']['token']='[redacted]'
        async def secured_send(message):
            if message['type']=='http.response.start':
                headers=MutableHeaders(scope=message)
                headers['Referrer-Policy']='no-referrer'
                headers['Content-Security-Policy']=CSP
                headers['X-Content-Type-Options']='nosniff'
                headers['X-Frame-Options']='DENY'
                headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
                if private:
                    headers['Cache-Control']='no-store'
                redact()
            await send(message)
        try:
            await self.app(scope,receive,secured_send)
        finally:
            redact()
