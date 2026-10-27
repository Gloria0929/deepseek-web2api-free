"""Live-only loopback harness: real app/adapter, counters contain no file IDs."""
import argparse
import threading
from urllib.parse import urlsplit
import uvicorn
from fastapi import Request
from curl_cffi import requests
from deepseek_files import DeepSeekFilesClient

import server

app, controller = server.app, None
rows, sessions, uploads, lock = [], {}, [0], threading.Lock()
failures = []
original_upload, original_request = DeepSeekFilesClient.upload, requests.Session.request
original_prepare = server.ChatService.prepare


def track_prepare(*args,**kwargs):
    try: return original_prepare(*args,**kwargs)
    except Exception as error:
        text = str(error).lower()
        categories = [name for name in ('authorization','invalid token','timeout','certificate','resolve',
            'proxy','connection','wasm','pow','challenge','403','429','rate','waf') if name in text]
        with lock: failures.append({'phase':'prepare','type':type(error).__name__,'categories':categories})
        raise


def track_upload(self,*args,**kwargs):
    result = original_upload(self,*args,**kwargs)
    with lock: uploads[0] += 1
    return result


def track_request(self,method,url,*args,**kwargs):
    if urlsplit(url).path == '/api/v0/chat/completion':
        body = kwargs.get('json') or {}
        with lock:
            sid = body.get('chat_session_id'); sessions.setdefault(sid,len(sessions)+1)
            rows.append({'session':sessions[sid],'refs':len(body.get('ref_file_ids',[])),
                         'has_parent':body.get('parent_message_id') is not None})
    return original_request(self,method,url,*args,**kwargs)


DeepSeekFilesClient.upload, requests.Session.request = track_upload, track_request
server.ChatService.prepare = track_prepare


@app.get('/__test__/state')
async def state(request: Request):
    server._check_api_auth(request)
    with lock: return {'uploads':uploads[0],'requests':list(rows),'failures':list(failures)}


@app.post('/__test__/shutdown')
async def shutdown(request: Request):
    server._check_api_auth(request); controller.should_exit=True
    return {'ok':True}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,required=True)
    controller=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=parser.parse_args().port,
        log_level='critical',access_log=False)); controller.run()
