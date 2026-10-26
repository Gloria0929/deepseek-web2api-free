import asyncio
import threading
from types import SimpleNamespace

import pytest

import server


@pytest.mark.parametrize('stage', ['acquire', 'prepare'])
def test_cancellation_leaves_release_owned_by_unfinished_worker(monkeypatch, stage):
    entered, unblock, finished = threading.Event(), threading.Event(), threading.Event()
    released = []
    acquired = SimpleNamespace(acct=object(), release=lambda: released.append(True))
    def acquire(**kw):
        if stage == 'acquire':
            entered.set(); unblock.wait(5)
        return acquired
    def prepare(*a, **kw):
        if stage == 'prepare':
            entered.set(); unblock.wait(5)
        return object()
    async def worker(function):
        def wrapped():
            try:
                return function()
            finally:
                finished.set()
        return await asyncio.to_thread(wrapped)
    monkeypatch.setattr(server, '_acquire', acquire)
    monkeypatch.setattr(server, 'prepare_turn', prepare)
    monkeypatch.setattr(server, 'run_in_threadpool', worker)
    async def run():
        task = asyncio.create_task(server._prepare_stream_account('prompt', (), None, False, 'default'))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            premature = list(released)
        finally:
            unblock.set()
            assert await asyncio.to_thread(finished.wait, 3)
        assert premature == [], 'Account must not be released while preparation is running'
        assert released == [True], 'Cancelled worker must release its eventual acquisition exactly once'
    asyncio.run(run())


def test_stream_cancel_waits_for_active_read_and_closes_before_release():
    entered, unblock, closed = threading.Event(), threading.Event(), threading.Event()
    released = []
    def upstream():
        try:
            entered.set(); unblock.wait(5)
            yield 'token'
        finally:
            closed.set()
    async def consume():
        try:
            async for _ in server._iterate_upstream(upstream()):
                pass
        finally:
            released.append(closed.is_set())
    async def run():
        task = asyncio.create_task(consume())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not released
        finally:
            unblock.set()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert released == [True]
    asyncio.run(run())


def test_response_cancel_during_send_closes_body_before_account_release():
    closed, releases = [], []
    async def body():
        try:
            yield b'chunk'
        finally:
            closed.append(True)
    async def send(message):
        if message['type'] == 'http.response.body':
            raise asyncio.CancelledError()
    async def receive():
        return {'type': 'http.disconnect'}
    acquired = SimpleNamespace(release=lambda: releases.append(bool(closed)))
    async def run():
        response = server._AccountStreamingResponse(acquired, body())
        with pytest.raises(asyncio.CancelledError):
            await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
        assert releases == [True]
    asyncio.run(run())
