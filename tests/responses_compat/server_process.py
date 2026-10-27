"""An owned loopback subprocess; cleanup never enumerates other processes."""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
KEY = 'compat-test-key'


@dataclass
class ServerHandle:
    url: str
    process: subprocess.Popen
    key: str = KEY

    def request(self,path,body=None):
        request = urllib.request.Request(self.url+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=3) as response:
            return json.load(response)

    def state(self): return self.request('/__test__/state')
    def mode(self,mode): return self.request('/__test__/mode',{'mode':mode})


@contextmanager
def running_server(*, accounts=None, key=KEY, live=False):
    with tempfile.TemporaryDirectory(prefix='responses-compat-') as directory:
        temp = Path(directory)
        account_path = temp/'accounts.json'
        account_path.write_text(json.dumps(accounts if accounts is not None else {'version':1,'accounts':[{'email':'synthetic',
            'token':'compat-synthetic-credential','cookies':'synthetic=yes'}]}),encoding='utf-8')
        env = {**os.environ,'ACCOUNT_STORE_PATH':str(account_path),'API_KEY_STORE_PATH':str(temp/'keys.sqlite3'),
            'API_KEYS':key,'ALLOW_UNAUTHENTICATED_API':'false','DEEPSEEK_ADMIN_PASSWORD':'compat-test-admin-password',
            'HOST':'127.0.0.1','LOG_LEVEL':'CRITICAL','THINKING':'true','SEARCH':'false','MODE':'auto','MODEL_ROUTES':'',
            'CLIENT_RPM_PER_KEY':'0','CLIENT_RPM_PER_IP':'0','PYTHONUTF8':'1','NO_PROXY':'127.0.0.1,localhost'}
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0)); port = probe.getsockname()[1]
        with (temp/'server.log').open('w',encoding='utf-8') as log:
            module = 'tests.responses_compat.live_server' if live else 'tests.responses_compat.server'
            process = subprocess.Popen([sys.executable,'-m',module,'--port',str(port)],
                cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            handle = ServerHandle(f'http://127.0.0.1:{port}',process,key)
            try:
                deadline = time.monotonic()+20
                while time.monotonic() < deadline:
                    if process.poll() is not None: raise RuntimeError('Test server failed: '+(temp/'server.log').read_text(encoding='utf-8')[-1500:])
                    try: handle.state(); break
                    except Exception: time.sleep(.05)
                else: raise TimeoutError('Test server startup timed out')
                yield handle
            finally:
                if process.poll() is None:
                    try: handle.request('/__test__/shutdown', {})
                    except Exception: process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
