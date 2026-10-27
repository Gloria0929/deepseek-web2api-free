"""Test-only entry point: startup environment is owned by server_process."""
import argparse
import threading

from fastapi import HTTPException, Request
import uvicorn

import server
from .backend import ScriptedAdapter

app = server.app
controller = None
adapter = ScriptedAdapter()
for account in server.pool._accounts: account._adapter = adapter


@app.get('/__test__/state')
async def state(request: Request):
    server._check_api_auth(request)
    with adapter.lock:
        return {'active':adapter.active,'closed':adapter.closed,'uploads':adapter.uploads,
            'busy':sum(a.state == 'busy' for a in server.pool._accounts),
            'stored':len(server.RESPONSE_STORE._records),'requests':list(adapter.requests)}


@app.post('/__test__/mode')
async def mode(request: Request):
    server._check_api_auth(request)
    body = await request.json()
    if body.get('mode') == 'busy':
        for account in server.pool._accounts: account.state = 'busy'
    elif body.get('mode') == 'idle':
        for account in server.pool._accounts: account.state = 'idle'
    elif body.get('mode') == 'credentials':
        for account in server.pool._accounts: account.token += '-changed'
    elif body.get('mode') == 'slow_prepare': adapter.prepare_delay = .8
    elif body.get('mode') == 'normal': adapter.prepare_delay = 0
    else: raise HTTPException(400,'Invalid test mode')
    return {'ok':True}


@app.post('/__test__/shutdown')
async def shutdown(request: Request):
    server._check_api_auth(request)
    controller.should_exit = True
    return {'ok':True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--port',type=int,required=True)
    controller = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=parser.parse_args().port,
        log_level='critical',access_log=False))
    controller.run()
