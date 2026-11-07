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
    args=parser.parse_args()
    if args.public_tunnel and not args.public_reporting:
        parser.error('--public-tunnel requires --public-reporting')
    if args.phone_uat and not args.public_reporting:
        parser.error('--phone-uat requires --public-reporting')
    root=Path(__file__).resolve().parent.parent
    sys.path.insert(0,str(root))
    from dotenv import load_dotenv
    load_dotenv(root/'.env',override=True)
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
