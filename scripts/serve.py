"""Use the user's set-secrets configuration instead of stale inherited credentials."""
from pathlib import Path
import argparse
import sys

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--uat',action='store_true')
    parser.add_argument('--public-reporting',action='store_true')
    parser.add_argument('--public-port',type=int,default=8001)
    parser.add_argument('--public-tunnel',action='store_true')
    parser.add_argument('--phone-uat',action='store_true')
    parser.add_argument('--demo-tunnel',action='store_true')
    parser.add_argument('--protected-tunnel',action='store_true')
    parser.add_argument('--public-base',default='')
    args=parser.parse_args()
    if args.public_tunnel and not args.public_reporting:
        parser.error('--public-tunnel requires --public-reporting')
    if args.phone_uat and not args.public_reporting:
        parser.error('--phone-uat requires --public-reporting')
    root=Path(__file__).resolve().parent.parent
    sys.path.insert(0,str(root))
    from dotenv import load_dotenv
    load_dotenv(root/'.env',override=True)
    if args.demo_tunnel or args.protected_tunnel:
        if args.demo_tunnel and args.protected_tunnel or args.public_reporting or args.uat or args.phone_uat:
            parser.error('Choose one isolated deployment mode')
        import os
        import uvicorn
        from scripts.public_gateway import Boundary,ProtectedTunnel,authorities
        authorities(args.port,args.public_base)
        if not args.public_base:
            parser.error('Deployment requires --public-base HTTPS origin')
        os.environ['DEMO_PUBLIC']='1' if args.demo_tunnel else '0'
        os.environ['PUBLIC_BASE_URL']=args.public_base
        from agents.app import create_app
        application=create_app(public_demo=args.demo_tunnel,background_scout=False)
        if args.demo_tunnel:
            application.state.config['public_reporting_enabled']=True
            application.state.config['public_report_url']=args.public_base+'/lapor'
            boundary=Boundary(application,port=args.port,public=True,public_base=args.public_base,dashboard_demo=True)
        else:
            boundary=ProtectedTunnel(application,port=args.port,public_base=args.public_base)
        uvicorn.run(boundary,host='127.0.0.1',port=args.port,access_log=False,proxy_headers=False,ws_max_size=65536,ws_max_queue=4)
        return
    if args.public_reporting:
        if args.uat or args.public_port == args.port:
            parser.error('Public reporting requires separate local/public ports and no UAT callbacks')
        import asyncio
        import os
        from agents.app import create_app
        from scripts.public_gateway import serve_pair
        application=create_app(public_demo=True,background_scout=False)
        application.state.config['public_reporting_enabled']=True
        # A tunnel must be explicitly started by the operator; this only defines the allowed origin.
        public_base=os.environ.get('PUBLIC_BASE_URL','').strip() if args.public_tunnel else ''
        if args.public_tunnel and not public_base:
            parser.error('Public tunnel origin is not configured')
        application.state.config['public_report_url']=(public_base or f'http://localhost:{args.public_port}')+'/lapor'
        protected=create_app(public_demo=False,background_scout=False) if args.phone_uat else None
        asyncio.run(serve_pair(application,port=args.port,public_port=args.public_port,public_base=public_base,protected=protected))
        return
    import uvicorn
    from scripts.public_gateway import Boundary
    if args.uat:
        from scripts.serve_uat import application
        application=application()
    else:
        from agents.app import create_app
        application=create_app()
    uvicorn.run(Boundary(application,port=args.port),host='127.0.0.1',port=args.port,access_log=False,proxy_headers=False)

if __name__=='__main__':
    main()
