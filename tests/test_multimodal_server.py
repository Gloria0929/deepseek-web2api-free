import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server
from image_inputs import ImageError
from session_cache import SessionCache
from tests.test_image_inputs import data_url


@pytest.fixture
def backend(monkeypatch):
    uploads, calls, released, poisoned = [], [], [], []
    files = SimpleNamespace(
        upload=lambda asset, **kw: uploads.append(asset.digest) or 'file-test',
        wait_ready=lambda ids: None)
    def chat(session, prompt, **kw):
        calls.append((prompt, kw))
        kw['ready_out'].update(response_message_id=2, session_id=session)
        return 'red circle', 'reasoning'
    def stream(session, prompt, **kw):
        chat(session, prompt, **kw)
        yield {'__type': 'thinking', 'content': 'reasoning'}
        yield 'red circle'
    adapter = SimpleNamespace(files=files, create_session=lambda: 'session', chat=chat, chat_stream=stream)
    account = SimpleNamespace(id='account', adapter=adapter)
    monkeypatch.setattr(server, '_check_api_auth', lambda _: None)
    monkeypatch.setattr(server, '_check_rate_limit', lambda _: {})
    monkeypatch.setattr(server, 'SESSION_CACHE', SessionCache(ttl=60))
    monkeypatch.setattr(server, '_acquire', lambda cache_key=None: server.AcquiredAccount(account, cache_key))
    monkeypatch.setattr(server.AcquiredAccount, 'release', lambda self: released.append(True))
    monkeypatch.setattr(server.pool, 'mark_error', lambda *a: poisoned.append(a))
    return SimpleNamespace(uploads=uploads, calls=calls, released=released, poisoned=poisoned, files=files)


def request(protocol, stream, nested=False):
    if protocol == 'openai':
        return '/v1/chat/completions', {'model': 'deepseek-chat', 'stream': stream,
            'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'what is this?'},
                {'type': 'image_url', 'image_url': {'url': data_url()}}]}]}
    image = {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                        'data': data_url().split(',', 1)[1]}}
    content = [{'type': 'text', 'text': 'what is this?'}, image]
    if nested:
        content = [{'type': 'tool_result', 'tool_use_id': 'tool-123', 'content': content}]
    return '/v1/messages', {'model': 'deepseek-chat', 'stream': stream, 'max_tokens': 200,
                             'messages': [{'role': 'user', 'content': content}]}


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
@pytest.mark.parametrize('stream', [False, True])
def test_images_reach_upstream_in_all_protocol_modes(backend, protocol, stream):
    path, body = request(protocol, stream)
    with TestClient(server.app) as client:
        response = client.post(path, json=body)
    assert response.status_code == 200
    assert 'red circle' in response.text
    assert len(backend.uploads) == 1
    prompt, options = backend.calls[0]
    assert '[Attached image 1;' in prompt and 'base64' not in prompt
    assert options['ref_file_ids'] == ['file-test']
    assert backend.released


def test_nested_anthropic_tool_image_reaches_shared_preparer(backend):
    path, body = request('anthropic', False, nested=True)
    with TestClient(server.app) as client:
        assert client.post(path, json=body).status_code == 200
    assert 'call_id=tool-123' in backend.calls[0][0]
    assert backend.uploads


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
@pytest.mark.parametrize('stream', [False, True])
def test_upload_failure_returns_error_before_stream_and_releases(backend, protocol, stream):
    def fail(*a, **k):
        raise ImageError('Image processing timeout', 504)
    backend.files.wait_ready = fail
    path, body = request(protocol, stream)
    with TestClient(server.app) as client:
        response = client.post(path, json=body)
    assert response.status_code == 504
    assert response.headers['content-type'].startswith('application/json')
    assert response.json()['error']['message'] == 'Image processing timeout'
    if protocol == 'anthropic':
        assert response.json()['type'] == 'error'
    assert not backend.calls and not backend.poisoned
    assert backend.released


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
def test_request_body_is_bounded_before_parsing(monkeypatch, protocol):
    monkeypatch.setenv('IMAGE_MAX_REQUEST_BYTES', '100')
    path = '/v1/messages' if protocol == 'anthropic' else '/v1/chat/completions'
    with TestClient(server.app) as client:
        response = client.post(path, content=b'x' * 101, headers={'Content-Type': 'application/json'})
    assert response.status_code == 413
    assert 'error' in response.json()


def test_anthropic_system_images_are_explicitly_rejected(backend):
    path, body = request('anthropic', False)
    body['system'] = [body['messages'][0]['content'][1]]
    with TestClient(server.app) as client:
        response = client.post(path, json=body)
    assert response.status_code == 400
    assert not backend.uploads and not backend.calls


def test_finished_stream_closes_transport_before_account_release(backend, monkeypatch):
    closed, release_order = [], []
    acquired = server._acquire()
    def stream(*args, **kwargs):
        try:
            yield 'red circle'
            yield {'__type': 'status', 'status': 'FINISHED'}
        finally:
            closed.append(True)
    acquired.adapter.chat_stream = stream
    monkeypatch.setattr(server, '_acquire', lambda **kw: acquired)
    monkeypatch.setattr(server.AcquiredAccount, 'release', lambda self: release_order.append(bool(closed)))
    path, body = request('openai', True)
    with TestClient(server.app) as client:
        response = client.post(path, json=body)
    assert response.status_code == 200
    assert release_order and all(release_order)
