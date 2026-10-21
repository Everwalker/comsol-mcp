"""Reproducible real stdio MCP W02 client; server must already be registered."""
import argparse, asyncio, json, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT = Path(__file__).resolve().parents[1]
async def main(args):
    run = ROOT/'evidence/w02/runs'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    env = dict(os.environ, COMSOL_SERVER_MCP_HOME=str(ROOT/'.phase1-private/mcp-w02'))
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'mcp_server.py')],env=env,cwd=str(ROOT))
    request={'run_directory':str(run),'known_server_pid':args.pid,'known_port':args.port,'preferences_directory':args.prefs}
    with (ROOT/'evidence/w02/mcp_stderr.log').open('a') as log:
        async with stdio_client(params,errlog=log) as (read,write):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(minutes=10)) as session:
                init=await session.initialize()
                result=await session.call_tool('runtime_poc_v64',request)
    run.mkdir(parents=True,exist_ok=True)
    (run/'mcp_transcript.json').write_text(json.dumps({'initialize':init.model_dump(mode='json'),'request':request,'response':result.model_dump(mode='json')},indent=2))
    print(json.dumps({'evidence':str(run),'isError':result.isError,'content':result.model_dump(mode='json')},indent=2))
    return 1 if result.isError else 0
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--pid',type=int,required=True);p.add_argument('--port',type=int,required=True);p.add_argument('--prefs',required=True)
    raise SystemExit(asyncio.run(main(p.parse_args())))
