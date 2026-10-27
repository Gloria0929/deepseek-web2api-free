"""Opt-in authorized HAR validation; credentials stay in temporary files only."""
import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_responses_compat import run_driver
from tests.responses_compat.server_process import running_server


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--har',type=Path,required=True)
    parser.add_argument('--client',choices=['python','javascript','all'],default='all')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    try:
        entries=json.loads(args.har.read_text(encoding='utf-8-sig'))['log']['entries']
        entry=[e for e in entries if urlsplit(e['request']['url']).path=='/api/v0/chat/completion'][-1]
        headers={h['name'].lower():h['value'] for h in entry['request']['headers']}
        accounts={'version':1,'accounts':[{'email':'live-validation','token':headers['authorization'].removeprefix('Bearer ').strip(),'cookies':headers['cookie']}]}
    except Exception as error:
        print(json.dumps({'passed':False,'failure_type':type(error).__name__}));return 1
    results={}
    with tempfile.TemporaryDirectory(prefix='responses-probes-') as directory:
        from PIL import Image,ImageDraw
        probes={}
        for name in ('red','blue'):
            canvas=Image.new('RGB',(384,256),'white');draw=ImageDraw.Draw(canvas)
            if name=='red':draw.ellipse((128,64,256,192),fill='red')
            else:
                draw.rectangle((64,88,144,168),fill='blue');draw.rectangle((240,88,320,168),fill='blue')
            buffer=BytesIO();canvas.save(buffer,format='PNG')
            probes[name]='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()
        probe_path=Path(directory)/'probes.json';probe_path.write_text(json.dumps(probes))
        with running_server(accounts=accounts,key=secrets.token_urlsafe(32),live=True) as backend:
            commands={'python':[sys.executable,'-m','tests.responses_compat.live_python'],
                'javascript':[shutil.which('node') or 'node',str(ROOT/'tests/responses_compat/live_javascript.mjs')]}
            for name,command in commands.items():
                if args.client not in ('all',name):continue
                row=run_driver(command+['--base-url',backend.url,'--api-key',backend.key,'--probes',str(probe_path)],timeout=600)
                # Child validators redact failures themselves; do not print stderr from live SDKs.
                row.pop('error',None);results[name]=row
                print(json.dumps({'client':name,**row},ensure_ascii=True),flush=True)
            transport=backend.state()
    report={'passed':bool(results) and all(r['passed'] for r in results.values()),'clients':results,'transport':transport}
    if args.report:args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__=='__main__':
    try: result=main()
    except Exception as error:
        print(json.dumps({'passed':False,'failure_type':type(error).__name__}));result=1
    raise SystemExit(result)
