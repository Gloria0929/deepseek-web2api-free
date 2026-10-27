from types import SimpleNamespace
import json

import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

import server
from response_store import ResponseStore
from tests.test_image_inputs import data_url


@pytest.fixture
def backend(monkeypatch):
    calls, uploads, releases = [], [], []
    state = SimpleNamespace(text='answer', reasoning='thinking', fail=False)
    def chat(session, prompt, **options):
        calls.append((session, prompt, options))
        if state.fail: raise RuntimeError('private upstream detail')
        return state.text, state.reasoning
    def stream(session, prompt, **options):
        text, reasoning = chat(session, prompt, **options)
        if reasoning: yield {'__type': 'thinking', 'content': reasoning}
        yield text
    adapter = SimpleNamespace(create_session=lambda: 'session-' + str(len(calls)), chat=chat,
        chat_stream=stream, files=SimpleNamespace(upload=lambda asset, **kw: uploads.append(asset.digest) or 'file-test', wait_ready=lambda ids: None))
    account = SimpleNamespace(id='account', adapter=adapter)
    monkeypatch.setattr(server, '_check_api_auth', lambda _: None)
    monkeypatch.setattr(server, '_check_rate_limit', lambda _: {})
    monkeypatch.setattr(server, 'RESPONSE_STORE', ResponseStore(), raising=False)
    monkeypatch.setattr(server, '_acquire_response', lambda parent: server.AcquiredAccount(account), raising=False)
    monkeypatch.setattr(server.AcquiredAccount, 'release', lambda self: releases.append(True))
    monkeypatch.setattr(server.pool, 'mark_error', lambda *args: None)
    return SimpleNamespace(calls=calls, uploads=uploads, releases=releases, state=state)


def sdk(client):
    return OpenAI(base_url='http://testserver/v1', api_key='caller-a', http_client=client,
                  max_retries=0, _strict_response_validation=True)


@pytest.mark.parametrize('stream', [False, True])
def test_sdk_text_and_stream_aggregation(backend, stream):
    with TestClient(server.app) as http:
        client = sdk(http)
        if stream:
            with client.responses.stream(model='deepseek-chat', input='hello') as events:
                observed = list(events)
                result = events.get_final_response()
            sequences = [e.sequence_number for e in observed if hasattr(e, 'sequence_number')]
            assert sequences == list(range(len(sequences)))
            assert any(e.type == 'response.reasoning_text.delta' for e in observed)
        else: result = client.responses.create(model='deepseek-chat', input='hello')
        assert result.output_text == 'answer'
        assert result.status == 'completed'
        assert client.responses.retrieve(result.id).output_text == 'answer'
        client.responses.delete(result.id)
        assert http.get('/v1/responses/' + result.id, headers={'Authorization':'Bearer caller-a'}).status_code == 404
    assert backend.releases


def test_snapshot_branch_and_instruction_replacement(backend):
    with TestClient(server.app) as http:
        client = sdk(http)
        parent = client.responses.create(input='original', instructions='old-rule')
        left = client.responses.create(input='left', previous_response_id=parent.id, instructions='new-rule')
        client.responses.create(input='right', previous_response_id=parent.id)
        assert 'old-rule' not in backend.calls[1][1] and 'new-rule' in backend.calls[1][1]
        assert 'left' not in backend.calls[2][1] and 'original' in backend.calls[2][1]
        other = http.get('/v1/responses/' + left.id, headers={'Authorization': 'Bearer caller-b'})
        assert other.status_code == 404


@pytest.mark.parametrize('stream', [False, True])
def test_image_replay_reuses_bound_file(backend, stream):
    with TestClient(server.app) as http:
        client = sdk(http)
        first = client.responses.create(input=[{'role':'user','content':[
            {'type':'input_text','text':'remember image'}, {'type':'input_image','image_url':data_url()}]}])
        if stream:
            with client.responses.stream(model='deepseek-chat', input='describe again', previous_response_id=first.id) as events:
                list(events); events.get_final_response()
        else: client.responses.create(input='describe again', previous_response_id=first.id)
    assert len(backend.uploads) == 1
    assert backend.calls[1][2]['ref_file_ids'] == ['file-test']


def test_store_false_failure_and_unsupported(backend):
    with TestClient(server.app) as http:
        result = http.post('/v1/responses', json={'input':'x', 'store':False}).json()
        assert http.get('/v1/responses/' + result['id']).status_code == 404
        assert http.post('/v1/responses', json={'input':'x','max_output_tokens':20}).status_code == 400
        backend.state.fail = True
        failure = http.post('/v1/responses', json={'input':'x'})
        assert failure.status_code == 502 and 'private' not in failure.text
        failure = http.post('/v1/responses', json={'input':'x','stream':True})
        assert 'response.failed' in failure.text and 'response.completed' not in failure.text


@pytest.mark.parametrize('stream', [False, True])
def test_sdk_function_loop_preserves_ids_schema_and_full_result(backend, stream):
    backend.state.text = '<|DSML|tool_calls><|DSML|invoke name="weather"><|DSML|parameter name="city"><![CDATA[Paris]]></|DSML|parameter></|DSML|invoke></|DSML|tool_calls>'
    tools = [{'type':'function','name':'weather','parameters':{'type':'object',
        'properties':{'city':{'type':'string'}},'required':['city'],'additionalProperties':False},'strict':True}]
    with TestClient(server.app) as http:
        client = sdk(http)
        if stream:
            with client.responses.stream(model='deepseek-chat', input='weather in Paris', tools=tools, tool_choice='required') as events:
                list(events); result = events.get_final_response()
        else: result = client.responses.create(input='weather in Paris', tools=tools, tool_choice='required')
        call = next(i for i in result.output if i.type == 'function_call')
        assert call.call_id != call.id and call.name == 'weather'
        assert '"city"' in backend.calls[0][1] and '"required"' in backend.calls[0][1]
        backend.state.text = 'sunny'
        final = client.responses.create(input=[{'type':'function_call_output','call_id':call.call_id,
            'output':'x' * 1500 + 'END-OF-RESULT'}], previous_response_id=result.id)
        assert 'END-OF-RESULT' in backend.calls[-1][1]
        assert call.call_id in backend.calls[-1][1] and final.output_text == 'sunny'


def test_limits_auth_and_changed_credentials(backend, monkeypatch):
    with TestClient(server.app) as http:
        parent = http.post('/v1/responses', json={'input':'x'}).json()
        monkeypatch.setattr(server, '_acquire_response', lambda _: server.AcquiredAccount(SimpleNamespace(
            id='other-account', adapter=SimpleNamespace())))
        result = http.post('/v1/responses', json={'input':'x','previous_response_id':parent['id']})
        assert result.status_code == 409
        monkeypatch.setenv('IMAGE_MAX_REQUEST_BYTES', '100')
        assert http.post('/v1/responses', content=b'x' * 101).status_code == 413
        def deny(_):
            from fastapi import HTTPException
            raise HTTPException(401, 'Invalid API key')
        monkeypatch.setattr(server, '_check_api_auth', deny)
        assert http.post('/v1/responses', json={'input':'x'}).status_code == 401


def batch_text(second='London'):
    return '<tool_calls>' + ''.join(f'<invoke name="weather"><parameter name="city"><![CDATA[{city}]]></parameter></invoke>'
        for city in ('Paris',second)) + '</tool_calls>'


@pytest.mark.parametrize('stream', [False,True])
def test_multiple_functions_reverse_results_and_effective_tools(backend, stream):
    backend.state.text = batch_text()
    tools = [{'type':'function','name':'weather','parameters':{'type':'object','properties':{'city':{'type':'string'}}}}]
    with TestClient(server.app) as http:
        client = sdk(http)
        if stream:
            with client.responses.stream(model='deepseek-chat',input='weather',tools=tools,parallel_tool_calls=True) as events:
                observed = list(events); response = events.get_final_response()
            indices = [e.output_index for e in observed if e.type == 'response.function_call_arguments.done']
            assert indices == sorted(set(indices)) and len(indices) == 2
        else: response = client.responses.create(input='weather',tools=tools,parallel_tool_calls=True)
        calls = [i for i in response.output if i.type == 'function_call']
        assert len(calls) == 2 and len({i.call_id for i in calls}) == 2
        assert response.parallel_tool_calls and response.tools[0].strict
        backend.state.text = 'both cities'
        outputs = [{'type':'function_call_output','call_id':i.call_id,'output':'result'} for i in reversed(calls)]
        final = client.responses.create(input=outputs,previous_response_id=response.id)
        assert final.output_text == 'both cities'
        assert backend.calls[-1][1].rindex(calls[1].call_id) < backend.calls[-1][1].rindex(calls[0].call_id)


def test_invalid_second_call_has_no_function_items_and_no_snapshot(backend):
    backend.state.text = batch_text('123')
    with TestClient(server.app) as http:
        response = http.post('/v1/responses',json={'input':'x','stream':True,'parallel_tool_calls':True,
            'tools':[{'type':'function','name':'weather','parameters':{'type':'object','properties':{'city':{'type':'string'}}}}]})
    assert response.status_code == 200
    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert [f['type'] for f in frames].count('response.failed') == 1
    assert not any(f['type'] == 'response.output_item.added' and f['item']['type'] == 'function_call' for f in frames)
    assert 'response.completed' not in response.text and not server.RESPONSE_STORE._records


@pytest.mark.parametrize('stream', [False, True])
def test_cancelled_response_is_not_stored_and_drains_worker(backend, stream):
    import asyncio
    import json
    import threading
    from starlette.requests import Request
    entered, unblock, closed = threading.Event(), threading.Event(), threading.Event()
    original_acquire = server._acquire_response
    def acquire(parent):
        acquired = original_acquire(parent)
        if not stream:
            def chat(*args, **kwargs):
                entered.set(); unblock.wait(5); closed.set()
                return 'answer', ''
            acquired.adapter.chat = chat
        return acquired
    server._acquire_response = acquire
    router = [r.original_router for r in server.app.routes if hasattr(r, 'original_router')][-1]
    endpoint = next(r.endpoint for r in router.routes if r.path == '/v1/responses')
    async def receive():
        return {'type':'http.request','body':json.dumps({'input':'x','stream':stream}).encode()}
    async def run():
        request = Request({'type':'http','method':'POST','path':'/v1/responses','headers':[],
                           'client':('test',1), 'asgi':{'spec_version':'2.4'}}, receive)
        if stream:
            response = await endpoint(request)
            async def send(message):
                if b'response.output_text.delta' in message.get('body', b''): raise asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError): await response(request.scope, receive, send)
        else:
            task = asyncio.create_task(endpoint(request))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                task.cancel()
                await asyncio.sleep(.02)
                assert not backend.releases
            finally: unblock.set()
            with pytest.raises(asyncio.CancelledError): await task
            assert closed.is_set()
        assert not server.RESPONSE_STORE._records
        assert backend.releases
    try: asyncio.run(run())
    finally: server._acquire_response = original_acquire
