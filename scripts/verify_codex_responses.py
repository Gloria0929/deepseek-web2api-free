"""Exercise an installed Codex CLI against the real loopback Responses stack.

Only the upstream is scripted. The tool case reads a synthetic file inside an
owned temporary workspace with the CLI's read-only sandbox enabled.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.responses_compat.server_process import running_server


def run_case(cli, http, tool, *, variant=False):
    with tempfile.TemporaryDirectory(prefix='codex-responses-') as workspace:
        (Path(workspace)/'probe.txt').write_text('CODEX-PROBE', encoding='utf-8')
        config = {'model_provider':'compat', 'model_providers.compat.name':'Local compatibility probe',
            'model_providers.compat.base_url':http.url+'/v1',
            'model_providers.compat.wire_api':'responses', 'model_providers.compat.env_key':'CODEX_COMPAT_API_KEY',
            'model_providers.compat.requires_openai_auth':False, 'model_providers.compat.request_max_retries':0,
            'model_providers.compat.stream_max_retries':0, 'web_search':'disabled',
            'features.apps':False, 'features.plugins':False, 'features.multi_agent':False,
            'mcp_servers':{}, 'approval_policy':'never'}
        command = [cli, 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '--json',
            '--sandbox', 'read-only', '-C', workspace, '--model', 'deepseek-chat']
        for key, value in config.items(): command += ['-c', key+'='+json.dumps(value)]
        scenario = 'case:codex_tool_variant' if variant else 'case:codex_tool'
        command += [scenario+' Read probe.txt with shell_command, then repeat its content.' if tool else 'Reply briefly. Do not use tools.']
        env = {key:value for key,value in os.environ.items() if not key.startswith(('CODEX_', 'OPENAI_', 'MCP_'))}
        result = subprocess.run(command, env={**env, 'CODEX_COMPAT_API_KEY':http.key,
            'NO_PROXY':'127.0.0.1,localhost'}, capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=90, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        messages = [event['item'].get('text') for event in events if event.get('type')=='item.completed'
            and event.get('item',{}).get('type')=='agent_message']
        expected = 'CODEX-PROBE' if tool else 'answer'
        if result.returncode or expected not in messages:
            raise RuntimeError('CLI acceptance failed: '+json.dumps({'exit':result.returncode,
                'events':events, 'stderr':result.stderr[-3000:]}))
        if tool and not any(event.get('item',{}).get('type')=='command_execution' and
                event['item'].get('exit_code')==0 for event in events):
            raise RuntimeError('CLI did not complete the synthetic file read')
        return {'case':('tool_roundtrip_variant' if variant else 'tool_roundtrip') if tool else 'initial_stream', 'passed':True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', default=shutil.which('codex'), help='Installed Codex executable')
    parser.add_argument('--dsml-variant', action='store_true', help='Use repeated fullwidth DSML separators, calls/string attributes, streamed character by character')
    args = parser.parse_args()
    if not args.cli: parser.error('Codex CLI is required; provide --cli')
    with running_server() as http:
        results = [run_case(args.cli,http,False), run_case(args.cli,http,True,variant=args.dsml_variant)]
        state = http.state()
        assert state['closed'] == 3 and len(state['requests']) == 3, state
        assert state['active'] == state['busy'] == state['stored'] == 0, state
    print(json.dumps({'results':results}, ensure_ascii=False))


if __name__ == '__main__': main()
