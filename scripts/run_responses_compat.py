"""Run all SDK acceptance drivers against one owned, credential-free HTTP app."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run_driver(command, *, timeout=120):
    try:
        result = subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=timeout)
        rows = []
        for line in result.stdout.splitlines():
            try: rows.append(json.loads(line))
            except ValueError: pass
        return {'passed':result.returncode == 0 and bool(rows) and all(r.get('passed') for r in rows),
                'checks':rows,'exit_code':result.returncode,'error':result.stderr[-1800:] if result.returncode else None}
    except (OSError,subprocess.TimeoutExpired) as error:
        return {'passed':False,'error':type(error).__name__,'checks':[]}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--require-all',action='store_true')
    parser.add_argument('--client',choices=['python','async','javascript','all'],default='all')
    args = parser.parse_args()
    from tests.responses_compat.server_process import running_server
    commands = {'python':[sys.executable,'-m','tests.responses_compat.python_sync'],
        'async':[sys.executable,'-m','tests.responses_compat.python_async'],
        'javascript':[shutil.which('node') or 'node',str(ROOT/'tests/responses_compat/javascript.mjs')]}
    results = {}
    with running_server() as backend:
        for client, command in commands.items():
            if args.client not in ('all',client): continue
            result = run_driver(command+['--base-url',backend.url,'--api-key',backend.key])
            required = set(json.loads((ROOT/'tests/responses_compat/scenarios.json').read_text())['required_groups'])
            names = [row.get('name') for row in result['checks']]
            result['passed'] = result['passed'] and set(names) == required and len(names) == len(required)
            results[client] = result
            print(json.dumps({'client':client,**result},ensure_ascii=True),flush=True)
    return 0 if results and all(r['passed'] for r in results.values()) and (not args.require_all or len(results)==3) else 1


if __name__ == '__main__': raise SystemExit(main())
