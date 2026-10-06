"""quant.common.http_timeout: an unanswered request must give up instead of
hanging. Uses a local server that accepts connections and never replies."""
import socket
import threading
import time
import urllib.request

import pytest
import requests

from quant.common import http_timeout


@pytest.fixture
def silent_server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    conns = []

    def accept():
        while True:
            try:
                conns.append(srv.accept()[0])
            except OSError:
                return
    threading.Thread(target=accept, daemon=True).start()
    yield f"http://127.0.0.1:{srv.getsockname()[1]}/"
    srv.close()
    for c in conns:
        c.close()


@pytest.fixture
def restore_defaults():
    original_request = requests.Session.request
    original_socket = socket.getdefaulttimeout()
    yield
    requests.Session.request = original_request
    socket.setdefaulttimeout(original_socket)


class TestInstall:
    def test_requests_without_timeout_gives_up(self, silent_server, restore_defaults):
        http_timeout.install(0.5)
        t = time.monotonic()
        with pytest.raises(requests.exceptions.Timeout):
            requests.get(silent_server)
        assert time.monotonic() - t < 5

    def test_urlopen_without_timeout_gives_up(self, silent_server, restore_defaults):
        http_timeout.install(0.5)
        t = time.monotonic()
        with pytest.raises((TimeoutError, OSError)):
            urllib.request.urlopen(silent_server)
        assert time.monotonic() - t < 5

    def test_explicit_timeout_is_kept(self, silent_server, restore_defaults):
        http_timeout.install(30)
        t = time.monotonic()
        with pytest.raises(requests.exceptions.Timeout):
            requests.get(silent_server, timeout=0.3)
        assert time.monotonic() - t < 5

    def test_installing_twice_does_not_stack_wrappers(self, restore_defaults):
        http_timeout.install(1)
        first = requests.Session.request._without_default_timeout
        http_timeout.install(2)
        assert requests.Session.request._without_default_timeout is first
