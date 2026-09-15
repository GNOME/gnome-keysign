"""Shared pytest configuration and fixtures for the gnome-keysign test suite."""

import socket
import threading

import pytest


def check_internet(host="relay.magic-wormhole.io", port=443, timeout=1.0, join_timeout=1.5):
    """Return True if a TCP connection to *host*:*port* can be established.

    The connection attempt runs in a daemon thread so that a DNS resolution
    hang (common in network-restricted build environments) cannot block the
    entire test collection phase indefinitely.
    """
    res = [False]

    def _target():
        try:
            conn = socket.create_connection((host, port), timeout=timeout)
            conn.close()
            res[0] = True
        except Exception:
            pass

    t = threading.Thread(target=_target)
    t.daemon = True
    t.start()
    t.join(timeout=join_timeout)
    return res[0]


#: Evaluated once per test session at collection time.
HAVE_INTERNET = check_internet()


def pytest_collection_modifyitems(items):
    """Skip all tests marked ``internet`` when network is unavailable.

    Marking a whole module::

        pytestmark = pytest.mark.internet

    Marking a single test::

        @pytest.mark.internet
        def test_something(): ...
    """
    if HAVE_INTERNET:
        return
    skip = pytest.mark.skip(reason="No internet access (relay.magic-wormhole.io:443 unreachable)")
    for item in items:
        if item.get_closest_marker("internet"):
            item.add_marker(skip)
