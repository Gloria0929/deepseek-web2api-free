import pytest

from response_store import ResponseStore
from responses_errors import ResponseError


def put(store, name, parent=None, scope='caller', text='hello'):
    store.put(scope, {'id': name, 'status': 'completed'}, parent,
              [{'role': 'user', 'content': text}], 'account', 'fingerprint', {})


def test_immutable_branches_and_scoped_retrieval():
    store = ResponseStore()
    put(store, 'parent'); put(store, 'left', 'parent', text='left'); put(store, 'right', 'parent', text='right')
    assert [m['content'] for m in store.history('caller', 'left')] == ['hello', 'left']
    assert [m['content'] for m in store.history('caller', 'right')] == ['hello', 'right']
    record = store.get('caller', 'parent'); record['response']['status'] = 'broken'
    assert store.get('caller', 'parent')['response']['status'] == 'completed'
    with pytest.raises(ResponseError): store.get('other', 'parent')


def test_expiry_deletion_and_depth_are_explicit(monkeypatch):
    clock = [0.0]
    store = ResponseStore(ttl=10, clock=lambda: clock[0], max_depth=2)
    put(store, 'a'); put(store, 'b', 'a')
    with pytest.raises(ResponseError): put(store, 'c', 'b')
    store.delete('caller', 'a')
    with pytest.raises(ResponseError): store.history('caller', 'b')
    clock[0] = 11
    with pytest.raises(ResponseError): store.get('caller', 'b')


def test_count_byte_and_history_limits():
    store = ResponseStore(max_records=2, per_caller=1)
    put(store, 'a'); put(store, 'b')
    with pytest.raises(ResponseError): store.get('caller', 'a')
    assert store.get('caller', 'b')
    with pytest.raises(ResponseError): put(ResponseStore(max_bytes=10), 'large')
    store = ResponseStore(max_history_bytes=10)
    with pytest.raises(ResponseError): put(store, 'a')


def test_store_does_not_publish_a_child_with_missing_ancestor():
    store = ResponseStore()
    put(store, 'a'); put(store, 'b', 'a')
    store.delete('caller', 'a')
    with pytest.raises(ResponseError): put(store, 'c', 'b')
    with pytest.raises(ResponseError): store.get('caller', 'c')


def test_store_checks_ancestors_evicted_during_insertion():
    store = ResponseStore(max_records=2)
    put(store, 'a'); put(store, 'b', 'a')
    with pytest.raises(ResponseError): put(store, 'c', 'b')
    with pytest.raises(ResponseError): store.get('caller', 'c')


def test_store_does_not_publish_a_child_over_history_limits():
    store = ResponseStore(max_depth=1)
    put(store, 'a')
    with pytest.raises(ResponseError): put(store, 'b', 'a')
    store = ResponseStore(max_history_bytes=100)
    put(store, 'a', text='a')
    with pytest.raises(ResponseError): put(store, 'b', 'a', text='b' * 70)
