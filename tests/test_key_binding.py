"""Fork feature tests: a bound client key only ever uses its DeepSeek account."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import server
from server import _BOUND_ACCOUNT_CTX, _acquire


class _StubAccount:
    def __init__(self, account_id):
        self.id = account_id
        self.email = f"{account_id}@example.local"
        self.token = "dummy"
        self.cookies = "test=dummy"
        self.state = "idle"
        self.adapter = None


class _StubPool:
    """Minimal pool double: idle/busy states, no upstream calls."""

    def __init__(self, *account_ids):
        self.accounts = {aid: _StubAccount(aid) for aid in account_ids}
        self.busy = set()
        self.acquired = []

    def acquire_by_id(self, account_id):
        acct = self.accounts.get(account_id)
        if acct is None or account_id in self.busy:
            return None
        self.busy.add(account_id)
        self.acquired.append(account_id)
        return acct

    def acquire(self):
        for aid, acct in self.accounts.items():
            if aid not in self.busy:
                self.busy.add(aid)
                self.acquired.append(aid)
                return acct
        return None

    def get_by_id(self, account_id):
        return self.accounts.get(account_id)

    def release(self, acct):
        self.busy.discard(acct.id)


@pytest.fixture
def stub_pool(monkeypatch):
    pool = _StubPool("acc-a", "acc-b")
    monkeypatch.setattr(server, "pool", pool)
    return pool


def test_pinned_acquire_returns_only_the_bound_account(stub_pool):
    acquired = _acquire(pinned_account_id="acc-b")
    try:
        assert acquired.acct.id == "acc-b"
    finally:
        acquired.release()
    # Round-robin was never consulted: only the pinned account was touched.
    assert stub_pool.acquired == ["acc-b"]


def test_pinned_acquire_via_auth_context(stub_pool):
    token = _BOUND_ACCOUNT_CTX.set("acc-a")
    try:
        acquired = _acquire()
        try:
            assert acquired.acct.id == "acc-a"
        finally:
            acquired.release()
    finally:
        _BOUND_ACCOUNT_CTX.reset(token)
    assert stub_pool.acquired == ["acc-a"]


def test_pinned_acquire_never_falls_back_to_an_idle_account(stub_pool):
    stub_pool.busy.add("acc-b")  # bound account busy, the other one idle
    with pytest.raises(HTTPException) as excinfo:
        _acquire(pinned_account_id="acc-b")
    assert excinfo.value.status_code == 503
    assert "busy" in excinfo.value.detail
    assert stub_pool.acquired == []  # idle acc-a was NOT taken


def test_pinned_acquire_to_missing_account(stub_pool):
    with pytest.raises(HTTPException) as excinfo:
        _acquire(pinned_account_id="acc-missing")
    assert excinfo.value.status_code == 503
    assert "not configured" in excinfo.value.detail
    assert stub_pool.acquired == []


def test_unpinned_acquire_still_round_robins(stub_pool):
    stub_pool.busy.add("acc-a")
    acquired = _acquire()
    try:
        assert acquired.acct.id == "acc-b"
    finally:
        acquired.release()
