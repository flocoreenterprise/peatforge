"""Open fixture demo plus isolated HMAC-protected UAT callbacks; no live UI route."""
from contextlib import AsyncExitStack

CALLBACKS=frozenset(('/api/voice/twilio/twiml/start','/api/voice/twilio/twiml/gather','/api/voice/twilio/status'))

class UatApplication:
    def __init__(self,public,protected):
        self.public,self.protected=public,protected

    async def __call__(self,scope,receive,send):
        if scope['type']=='lifespan':
            async with AsyncExitStack() as stack:
                while True:
                    message=await receive()
                    if message['type']=='lifespan.startup':
                        try:
                            await stack.enter_async_context(self.public.router.lifespan_context(self.public))
                            await stack.enter_async_context(self.protected.router.lifespan_context(self.protected))
                        except Exception:
                            await send({'type':'lifespan.startup.failed','message':'UAT startup unavailable; configuration values suppressed.'})
                            return
                        await send({'type':'lifespan.startup.complete'})
                    elif message['type']=='lifespan.shutdown':
                        await stack.aclose()
                        await send({'type':'lifespan.shutdown.complete'})
                        return
        else:
            target=self.protected if scope['type']=='http' and scope.get('method')=='POST' and scope.get('path') in CALLBACKS else self.public
            await target(scope,receive,send)

def application():
    from agents.app import create_app
    public=create_app(public_demo=True,background_scout=False)
    protected=create_app(public_demo=False,background_scout=False)
    if public.state.database_path==protected.state.database_path:
        raise ValueError('UAT stores must be isolated')
    return UatApplication(public,protected)
