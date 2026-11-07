"""Use the user's set-secrets configuration instead of stale inherited credentials."""
from pathlib import Path
import argparse
import sys

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--uat',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    sys.path.insert(0,str(root))
    from dotenv import load_dotenv
    load_dotenv(root/'.env',override=True)
    import uvicorn
    target='scripts.serve_uat:application' if args.uat else 'agents.app:app'
    uvicorn.run(target,factory=args.uat,host='127.0.0.1',port=args.port,access_log=False,proxy_headers=False)

if __name__=='__main__':
    main()
