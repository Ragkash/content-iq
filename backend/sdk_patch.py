"""
ContentIQ — SDK Timeout Patch
Import this module before any Azure SDK or OpenAI SDK usage.

Root cause: azure-core's RequestsTransport and the openai SDK's httpx client
do not set socket timeouts by default. On networks with slow proxy auto-detection
(common on Windows), this causes the first connection to block indefinitely —
Ctrl+C cannot interrupt it because Python is stuck in a C-level socket call.

Fix: patch requests.Session.send and httpx.Client/__init__ to inject default
timeouts. This is transparent to all SDK callers.
"""

import requests as _requests
import httpx as _httpx


# ── requests.Session timeout (azure-core uses RequestsTransport → requests) ──
_orig_requests_send = _requests.Session.send


def _patched_requests_send(self, request, **kwargs):
    kwargs.setdefault("timeout", (30, 120))  # (connect_timeout, read_timeout) seconds
    return _orig_requests_send(self, request, **kwargs)


_requests.Session.send = _patched_requests_send


# ── httpx.Client timeout (openai SDK uses httpx) ─────────────────────────────
_orig_httpx_client_init = _httpx.Client.__init__


def _patched_httpx_client_init(self, *args, **kwargs):
    if "timeout" not in kwargs:
        kwargs["timeout"] = _httpx.Timeout(120.0, connect=30.0)
    _orig_httpx_client_init(self, *args, **kwargs)


_httpx.Client.__init__ = _patched_httpx_client_init


# ── httpx.AsyncClient timeout (covers any async SDK paths) ───────────────────
_orig_httpx_async_init = _httpx.AsyncClient.__init__


def _patched_httpx_async_init(self, *args, **kwargs):
    if "timeout" not in kwargs:
        kwargs["timeout"] = _httpx.Timeout(120.0, connect=30.0)
    _orig_httpx_async_init(self, *args, **kwargs)


_httpx.AsyncClient.__init__ = _patched_httpx_async_init
