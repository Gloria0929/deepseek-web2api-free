from types import SimpleNamespace

import pytest
import threading

import image_sources as sources
from image_inputs import ImageError, ImageLimits


class Reply:
    def __init__(self, status=200, headers=None, chunks=()):
        self.status, self.headers = status, headers or {}
        self.chunks = iter(chunks)
        self.closed = False

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, size):
        return next(self.chunks, b'')

    def close(self):
        self.closed = True


def test_redirect_to_private_address_is_rejected_before_connect(monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1' if host == 'private.example' else '8.8.8.8', 443))])
    reply = Reply(302, {'Location': 'https://private.example/secret'})
    opened, closed = [], []
    def open_connection(target, timeout):
        opened.append(target.address)
        return SimpleNamespace(sock=None, close=lambda: closed.append(True)), reply
    monkeypatch.setattr(sources, '_open', open_connection)
    with pytest.raises(ImageError):
        sources.download_image('https://public.example/a', ImageLimits())
    assert opened == ['8.8.8.8']
    assert closed == [True] and reply.closed


@pytest.mark.parametrize('headers,chunks,status', [
    ({'Content-Length': '100'}, (), 413),
    ({}, [b'a' * 6], 413),
    ({'Content-Encoding': 'gzip'}, (), 400),
])
def test_remote_size_and_encoding_limits_close_connection(monkeypatch, headers, chunks, status):
    reply = Reply(headers=headers, chunks=chunks)
    closed = []
    monkeypatch.setattr(sources, 'public_target', lambda *args: object())
    monkeypatch.setattr(sources, '_open', lambda *a: (SimpleNamespace(sock=None, close=lambda: closed.append(True)), reply))
    with pytest.raises(ImageError) as exc:
        sources.download_image('https://example.test/a', ImageLimits(max_bytes=5))
    assert exc.value.status == status
    assert reply.closed and closed


def test_connection_uses_checked_ip_but_hostname_for_tls_and_host(monkeypatch):
    connections, hosts, tls, requests, timeouts = [], [], [], [], []
    sock = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(sources.socket, 'create_connection', lambda addr, timeout: connections.append(addr) or sock)
    monkeypatch.setattr(sources.ssl, 'create_default_context', lambda: SimpleNamespace(
        wrap_socket=lambda s, server_hostname: tls.append(server_hostname) or s))
    class Connection:
        def __init__(self, host, port, timeout):
            hosts.append(host)
        def request(self, method, path, headers):
            requests.append(headers)
        def getresponse(self):
            return Reply()
        def close(self):
            pass
    monkeypatch.setattr(sources.http.client, 'HTTPConnection', Connection)
    sources._open(sources.PublicTarget('public.example', 443, '8.8.8.8', '/', True), 10)
    assert connections == [('8.8.8.8', 443)]
    assert hosts == tls == ['public.example']
    assert not any(k.lower() in ('cookie', 'authorization', 'proxy-authorization') for k in requests[0])


def test_dns_timeout_bounds_waiting_and_background_resolution(monkeypatch):
    entered, unblock, done = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def resolve(*args, **kwargs):
        calls.append(True); entered.set()
        try:
            unblock.wait(2)
            return []
        finally:
            done.set()
    monkeypatch.setattr(sources, '_DNS_SLOTS', threading.BoundedSemaphore(1))
    monkeypatch.setattr(sources.socket, 'getaddrinfo', resolve)
    try:
        with pytest.raises(ImageError, match='DNS'):
            sources.public_target('https://example.test/image', timeout=0.03)
        assert entered.is_set()
        with pytest.raises(ImageError, match='DNS'):
            sources.public_target('https://example.test/another', timeout=0.01)
        assert len(calls) == 1
    finally:
        unblock.set(); assert done.wait(2)
