"""Minimal HTTP helpers on top of the standard library.

Used by every provider adapter so Pellaeon has no third-party dependencies
inside ChimeraX's Python. Supports JSON requests and streamed line/SSE
responses with cooperative cancellation.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, Optional, Tuple

USER_AGENT = "Pellaeon/0.1 (ChimeraX assistant)"


class HttpError(Exception):
    def __init__(self, status: int, body: str, url: str = ""):
        self.status = status
        self.body = body
        self.url = url
        super().__init__("HTTP %s from %s: %s" % (status, url, body[:800]))


class Cancelled(Exception):
    pass


def _build_request(method: str, url: str, headers: Optional[Dict[str, str]],
                   body: Optional[Any]) -> urllib.request.Request:
    data = None
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        else:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
    return urllib.request.Request(url, data=data, headers=hdrs, method=method)


def _open(req: urllib.request.Request, timeout: float):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        raise HttpError(e.code, body, req.full_url) from None
    except urllib.error.URLError as e:
        raise ConnectionError("Could not reach %s: %s" % (req.full_url, e.reason)) from None


def request_json(method: str, url: str, headers: Optional[Dict[str, str]] = None,
                 body: Optional[Any] = None, timeout: float = 60) -> Any:
    req = _build_request(method, url, headers, body)
    with _open(req, timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HttpError(getattr(resp, "status", 200), "Non-JSON response: " + raw[:300], url)


def request_bytes(method: str, url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 60) -> bytes:
    req = _build_request(method, url, headers, None)
    with _open(req, timeout) as resp:
        return resp.read()


def stream_lines(method: str, url: str, headers: Optional[Dict[str, str]] = None,
                 body: Optional[Any] = None, timeout: float = 120,
                 cancel: Optional[threading.Event] = None) -> Iterator[str]:
    """Yield decoded lines (without trailing newline) as they arrive."""
    req = _build_request(method, url, headers, body)
    resp = _open(req, timeout)
    try:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            yield raw.decode("utf-8", "replace").rstrip("\r\n")
    finally:
        try:
            resp.close()
        except Exception:
            pass


def iter_sse(lines: Iterator[str]) -> Iterator[Tuple[str, str]]:
    """Turn a line stream into (event, data) tuples per the SSE spec."""
    event = ""
    data_lines = []
    for line in lines:
        if line == "":
            if data_lines:
                yield event or "message", "\n".join(data_lines)
            event = ""
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
        else:
            key, value = line, ""
        if key == "event":
            event = value
        elif key == "data":
            data_lines.append(value)
    if data_lines:
        yield event or "message", "\n".join(data_lines)


def iter_ndjson(lines: Iterator[str]) -> Iterator[Any]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue
