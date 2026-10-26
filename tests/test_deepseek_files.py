from types import SimpleNamespace
from itertools import chain, repeat

import pytest

from deepseek_files import DeepSeekFilesClient
from image_inputs import ImageAsset, ImageError


def response(data, status=200):
    return SimpleNamespace(status_code=status, json=lambda: data)


def envelope(files=None, **extra):
    return response({'code': 0, 'data': {'biz_code': 0, 'biz_data': {'files': files, **extra}}})


def test_upload_uses_multipart_fresh_target_and_closes_form(monkeypatch):
    import deepseek_files as module
    calls, parts, closed = [], [], []
    mime = SimpleNamespace(addpart=lambda *a, **k: parts.append((a, k)), close=lambda: closed.append(True))
    monkeypatch.setattr(module, 'CurlMime', lambda: mime)
    transport = SimpleNamespace(post=lambda *a, **k: calls.append((a, k)) or envelope(id='file-a'))
    targets = []
    def headers(target, include_hif):
        targets.append((target, include_hif))
        return {'Content-Type': 'application/json', 'Authorization': 'test'}
    client = DeepSeekFilesClient(transport, headers, {'Authorization': 'test'})
    asset = ImageAsset(b'abc', 'image/png', 'digest', 1, 1)
    assert client.upload(asset, thinking=True, model_type='default') == 'file-a'
    assert targets == [('/api/v0/file/upload_file', False)]
    sent = calls[0][1]
    assert 'Content-Type' not in sent['headers']
    assert sent['headers']['X-File-Size'] == '3'
    assert sent['headers']['X-Thinking-Enabled'] == '1'
    assert sent['multipart'] is mime
    assert parts[0][1]['data'] == b'abc'
    assert closed == [True]


def test_batch_polling_uses_repeated_parameters(monkeypatch):
    import deepseek_files as module
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    replies = iter([envelope([{'id': 'a', 'status': 'PARSING'}, {'id': 'b', 'status': 'SUCCESS'}]),
                    envelope([{'id': 'a', 'status': 'SUCCESS'}, {'id': 'b', 'status': 'SUCCESS'}])])
    calls = []
    client = DeepSeekFilesClient(SimpleNamespace(get=lambda *a, **k: calls.append(k) or next(replies)), None, {})
    client.wait_ready(['a', 'b'])
    assert len(calls) == 2
    assert calls[0]['params'] == [('file_ids', 'a'), ('file_ids', 'b')]


@pytest.mark.parametrize('reply,status', [
    (response({'code': 40003, 'msg': 'invalid token'}), 502),
    (response({}, 429), 429),
    (envelope([{'id': 'a', 'status': 'FAILED', 'error_code': 'invalid'}]), 502),
    (envelope([]), 502),
    (envelope([{'id': 'a', 'status': 'SUCCESS'}, 'bad-entry']), 502),
])
def test_poll_errors_are_typed(reply, status):
    client = DeepSeekFilesClient(SimpleNamespace(get=lambda *a, **k: reply), None, {})
    with pytest.raises(ImageError) as exc:
        client.wait_ready(['a'])
    assert exc.value.status == status


def test_poll_timeout_is_bounded(monkeypatch):
    import deepseek_files as module
    ticks = chain([0, 0, 0], repeat(61))
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    client = DeepSeekFilesClient(SimpleNamespace(get=lambda *a, **k: envelope([{'id': 'a', 'status': 'PENDING'}])), None, {})
    with pytest.raises(ImageError) as exc:
        client.wait_ready(['a'])
    assert exc.value.status == 504
