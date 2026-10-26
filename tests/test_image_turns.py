from types import SimpleNamespace

import pytest

import server
from image_inputs import ImageAsset, ImageAttachment, ImageError
from session_cache import SessionCache
from turn_preparation import prepare_turn


def account(name='a', fail=False):
    uploads = []
    def upload(asset, **kw):
        uploads.append(asset.digest)
        return name + '-' + asset.digest
    def wait(ids):
        if fail:
            raise ImageError('processing failed', 502)
    adapter = SimpleNamespace(create_session=lambda: 'sess-' + name,
        files=SimpleNamespace(upload=upload, wait_ready=wait))
    return SimpleNamespace(id=name, adapter=adapter), uploads


def attachment(digest='red'):
    return ImageAttachment('[image:' + digest + ']', ImageAsset(b'bytes', 'image/png', digest, 1, 1))


def test_history_delta_uses_cached_files_and_only_new_refs(monkeypatch):
    monkeypatch.setattr(server, 'SESSION_CACHE', SessionCache(ttl=60))
    acct, uploads = account()
    a, b = attachment(), attachment('blue')
    first = server.AcquiredAccount(acct, 'conv')
    t = prepare_turn(first, 'User: ' + a.marker, (a,))
    assert t.ref_file_ids == ['a-red']
    assert server.SESSION_CACHE.get('conv').image_files == {}
    first.record_message_id(2, full_prompt='User: ' + a.marker)
    second = server.AcquiredAccount(acct, 'conv')
    full = 'User: ' + a.marker + '\nUser: ' + b.marker
    t = prepare_turn(second, full, (a, b))
    assert t.prompt == 'User: ' + b.marker
    assert t.ref_file_ids == ['a-blue']
    assert t.retry_prompt == full
    assert t.retry_ref_file_ids == ['a-red', 'a-blue']
    assert uploads == ['red', 'blue']


def test_identical_assets_upload_once_but_keep_reference_order():
    acct, uploads = account()
    a = attachment()
    t = prepare_turn(server.AcquiredAccount(acct), a.marker, (a, a))
    assert uploads == ['red']
    assert t.ref_file_ids == ['a-red', 'a-red']


def test_account_switch_does_not_reuse_file_ids(monkeypatch):
    monkeypatch.setattr(server, 'SESSION_CACHE', SessionCache(ttl=60))
    a = attachment()
    first_acct, _ = account('first')
    acq = server.AcquiredAccount(first_acct, 'conv')
    prepare_turn(acq, a.marker, (a,))
    acq.record_message_id(2, full_prompt=a.marker)
    other, uploads = account('second')
    t = prepare_turn(server.AcquiredAccount(other, 'conv'), a.marker, (a,))
    assert t.ref_file_ids == ['second-red']
    assert uploads == ['red']


def test_processing_failure_does_not_commit_images_or_prompt(monkeypatch):
    monkeypatch.setattr(server, 'SESSION_CACHE', SessionCache(ttl=60))
    acct, _ = account()
    first = server.AcquiredAccount(acct, 'conv')
    first.create_session()
    first.record_message_id(2, full_prompt='old prompt')
    acct.adapter.files.wait_ready = lambda _: (_ for _ in ()).throw(ImageError('failed', 502))
    with pytest.raises(ImageError):
        prepare_turn(server.AcquiredAccount(acct, 'conv'), attachment().marker, (attachment(),))
    cached = server.SESSION_CACHE.get('conv')
    assert cached.sent_prompt == 'old prompt'
    assert cached.image_files == {}


def test_text_followup_retains_previous_images_for_new_session_retry(monkeypatch):
    monkeypatch.setattr(server, 'SESSION_CACHE', SessionCache(ttl=60))
    acct, _ = account()
    acq = server.AcquiredAccount(acct, 'conv')
    prepare_turn(acq, attachment().marker, (attachment(),))
    acq.record_message_id(2, full_prompt=attachment().marker)
    t = prepare_turn(server.AcquiredAccount(acct, 'conv'), 'User: what color?', ())
    assert t.ref_file_ids == []
    assert t.retry_ref_file_ids == ['a-red']
    assert attachment().marker in t.retry_prompt


def test_latest_message_new_image_retry_keeps_previous_references(monkeypatch):
    monkeypatch.setattr(server, 'SESSION_CACHE', SessionCache(ttl=60))
    acct, _ = account()
    first = server.AcquiredAccount(acct, 'conv')
    prepare_turn(first, attachment().marker, (attachment(),))
    first.record_message_id(2, full_prompt=attachment().marker)
    blue = attachment('blue')
    turn = prepare_turn(server.AcquiredAccount(acct, 'conv'), blue.marker, (blue,))
    assert turn.ref_file_ids == ['a-blue']
    assert turn.retry_ref_file_ids == ['a-red', 'a-blue']


def test_credential_replacement_invalidates_cached_image_references(monkeypatch):
    monkeypatch.setattr(server, 'SESSION_CACHE', SessionCache(ttl=60))
    acct, _ = account()
    acct.token, acct.cookies = 'token-one', 'cookie-one'
    first = server.AcquiredAccount(acct, 'conv')
    prepare_turn(first, attachment().marker, (attachment(),))
    first.record_message_id(2, full_prompt=attachment().marker)
    acct.token = 'token-two'
    second = server.AcquiredAccount(acct, 'conv')
    assert second.image_files == {}
    assert second.previous_prompt == ''
