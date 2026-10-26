from types import SimpleNamespace

import pytest

import adapter as module
from adapter import DeepSeekAdapter, UpstreamEmptyError


@pytest.mark.parametrize('stream', [False, True])
def test_real_completion_bodies_include_image_references(monkeypatch, stream):
    ad = DeepSeekAdapter('test', '')
    calls = []
    resp = SimpleNamespace(status_code=200, headers={}, raise_for_status=lambda: None,
        iter_lines=lambda: iter([b'data: {"v":"answer"}']), close=lambda: None)
    client = SimpleNamespace(post=lambda *a, **k: calls.append(k) or resp)
    monkeypatch.setattr(ad, '_pow_headers', lambda *a, **k: {})
    monkeypatch.setattr(ad, '_client', client)
    monkeypatch.setattr(module.cffi_requests, 'Session', lambda **kw: client)
    if stream:
        assert list(ad._chat_stream_once('s', 'p', ref_file_ids=['file-a'])) == ['answer']
    else:
        ad._send_completion('s', 'p', ref_file_ids=['file-a'])
    assert calls[0]['json']['ref_file_ids'] == ['file-a']


@pytest.mark.parametrize('stream', [False, True])
def test_new_session_retry_replays_full_prompt_and_image_refs(monkeypatch, stream):
    ad = DeepSeekAdapter('test', '')
    calls = []
    def once(session, prompt, **kw):
        calls.append((session, prompt, kw))
        if len(calls) == 1:
            raise UpstreamEmptyError('empty')
        return ('answer', '')
    def stream_once(session, prompt, **kw):
        once(session, prompt, **kw)
        yield 'answer'
    monkeypatch.setattr(ad, 'create_session', lambda: 'new-session')
    monkeypatch.setattr(ad, '_chat_once', once)
    monkeypatch.setattr(ad, '_chat_stream_once', stream_once)
    kwargs = dict(parent_message_id=2, ref_file_ids=['file-b'], retry_prompt='complete history',
                  retry_ref_file_ids=['file-a', 'file-b'])
    if stream:
        assert list(ad.chat_stream('old-session', 'delta', **kwargs)) == ['answer']
    else:
        assert ad.chat('old-session', 'delta', **kwargs) == ('answer', '')
    assert calls[1][0:2] == ('new-session', 'complete history')
    assert calls[1][2]['ref_file_ids'] == ['file-a', 'file-b']
    assert calls[1][2]['parent_message_id'] is None
