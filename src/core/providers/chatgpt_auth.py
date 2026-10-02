"""Sign in with ChatGPT: the OAuth flow the Codex CLI uses, without ChimeraX.

Authorization code + PKCE against auth.openai.com with the Codex CLI's public
client id, a one-shot callback server on 127.0.0.1:1455, then the tokens go
into the SecretStore under the preset id "chatgpt" as one JSON string.
Tokens and the account e-mail are never logged; the e-mail is not kept at all.

Protocol reference: openai/codex, codex-rs/login (server.rs, oauth/*.rs,
token_data.rs) and codex-rs/login/src/auth/manager.rs.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, Optional

from ..http import request_json, HttpError

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"          # Codex CLI public client (no secret)
ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/oauth/authorize"
TOKEN_URL = ISSUER + "/oauth/token"
SCOPE = "openid profile email offline_access"
CALLBACK_PORT = 1455
CALLBACK_PATH = "/auth/callback"
ORIGINATOR = "codex_cli_rs"
STORE_KEY = "chatgpt"                                 # SecretStore slot
REFRESH_MARGIN = 5 * 60                               # refresh this many seconds before expiry
LOGIN_TIMEOUT = 10 * 60

_PLAN_NAMES = {
    "free": "Free", "go": "Go", "plus": "Plus", "pro": "Pro", "prolite": "Pro Lite", "promax": "Pro Max",
    "team": "Team", "business": "Business", "enterprise": "Enterprise", "hc": "Enterprise",
    "edu": "Education", "education": "Education", "edu_plus": "Education Plus", "edu_pro": "Education Pro",
}


class AuthError(Exception):
    """A sign-in step failed in a way the user should see."""


class PortBusy(AuthError):
    pass


class StateMismatch(AuthError):
    """A callback that does not belong to this attempt: refused, the attempt keeps waiting."""


# ---------------------------------------------------------------- PKCE / state / URLs
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> Dict[str, str]:
    verifier = _b64url(os.urandom(64))                          # 86 chars, within 43..128
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return {"verifier": verifier, "challenge": challenge}


def generate_state() -> str:
    return _b64url(os.urandom(32))


def redirect_uri(port: int = CALLBACK_PORT) -> str:
    return "http://localhost:%d%s" % (port, CALLBACK_PATH)


def build_authorize_url(challenge: str, state: str, redirect: str) -> str:
    params = [
        ("response_type", "code"),
        ("client_id", CLIENT_ID),
        ("redirect_uri", redirect),
        ("scope", SCOPE),
        ("code_challenge", challenge),
        ("code_challenge_method", "S256"),
        ("state", state),
        ("id_token_add_organizations", "true"),
        ("codex_cli_simplified_flow", "true"),
        ("originator", ORIGINATOR),
    ]
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


def parse_callback(target: str, expected_state: str) -> str:
    """The authorization code from a callback request target ('/auth/callback?code=..&state=..')."""
    parsed = urllib.parse.urlsplit(target)
    if parsed.path and parsed.path != CALLBACK_PATH:
        raise AuthError("unexpected callback path")
    q = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    if not secrets.compare_digest(q.get("state", ""), expected_state):
        raise StateMismatch("The sign-in reply did not match this request (state mismatch). Try again.")
    if q.get("error"):
        desc = q.get("error_description") or q["error"]
        if q["error"] == "access_denied":
            raise AuthError("Sign-in was cancelled.")
        raise AuthError("ChatGPT refused the sign-in: %s" % desc)
    code = q.get("code", "")
    if not code:
        raise AuthError("The sign-in reply carried no code.")
    return code


# ---------------------------------------------------------------- JWT claims
def decode_jwt_claims(token: str) -> Dict[str, Any]:
    """The payload of a JWT, unverified (the token came straight from auth.openai.com over TLS)."""
    parts = (token or "").split(".")
    if len(parts) != 3 or not parts[1]:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


def account_info(id_token: str, access_token: str = "") -> Dict[str, str]:
    """ChatGPT account id and plan from the id_token claims (falls back to the access token). No e-mail."""
    out = {"account_id": "", "plan": ""}
    for tok in (id_token, access_token):
        claims = decode_jwt_claims(tok)
        auth = claims.get("https://api.openai.com/auth") or {}
        if not isinstance(auth, dict):
            auth = {}
        if not out["account_id"]:
            out["account_id"] = str(auth.get("chatgpt_account_id") or claims.get("chatgpt_account_id") or "")
        if not out["plan"]:
            out["plan"] = str(auth.get("chatgpt_plan_type") or "")
        if out["account_id"] and out["plan"]:
            break
    return out


def plan_label(plan: str) -> str:
    p = (plan or "").strip().lower()
    return _PLAN_NAMES.get(p, p.replace("_", " ").title() if p else "")


def _expiry(resp: Dict[str, Any]) -> float:
    exp = decode_jwt_claims(resp.get("access_token", "")).get("exp")
    if isinstance(exp, (int, float)) and exp > 0:
        return float(exp)
    try:
        return time.time() + float(resp.get("expires_in") or 3600)
    except (TypeError, ValueError):
        return time.time() + 3600


def _tokens_from_response(resp: Dict[str, Any], previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not isinstance(resp, dict) or not resp.get("access_token"):
        raise AuthError("ChatGPT returned no access token.")
    previous = previous or {}
    id_token = resp.get("id_token") or previous.get("id_token") or ""
    info = account_info(id_token, resp["access_token"])
    return {
        "access_token": resp["access_token"],
        "refresh_token": resp.get("refresh_token") or previous.get("refresh_token") or "",
        "id_token": id_token,
        "expires_at": _expiry(resp),
        "account_id": info["account_id"] or previous.get("account_id", ""),
        "plan": info["plan"] or previous.get("plan", ""),
    }


# ---------------------------------------------------------------- token endpoint
Http = Callable[..., Any]   # request_json-compatible: (method, url, headers=, body=, timeout=)


def _token_error(e: HttpError) -> str:
    try:
        d = json.loads(e.body)
    except Exception:
        return e.body[:200]
    if not isinstance(d, dict):
        return e.body[:200]
    err = d.get("error")
    if isinstance(err, dict):
        err = err.get("message") or err.get("code")
    return str(d.get("error_description") or err or e.body[:200])


def exchange_code(code: str, verifier: str, redirect: str, http: Http = request_json) -> Dict[str, Any]:
    form = urllib.parse.urlencode({
        "grant_type": "authorization_code", "client_id": CLIENT_ID, "code": code,
        "redirect_uri": redirect, "code_verifier": verifier,
    }).encode("ascii")
    try:
        resp = http("POST", TOKEN_URL, headers={"Content-Type": "application/x-www-form-urlencoded"}, body=form, timeout=30)
    except HttpError as e:
        raise AuthError("ChatGPT did not accept the sign-in (%d): %s" % (e.status, _token_error(e)))
    except ConnectionError as e:
        raise AuthError("Could not reach auth.openai.com: %s" % e)
    return _tokens_from_response(resp)


def refresh_tokens(tokens: Dict[str, Any], http: Http = request_json) -> Dict[str, Any]:
    """New tokens from the refresh token; AuthError('...sign in again') when the grant is gone."""
    rt = (tokens or {}).get("refresh_token", "")
    if not rt:
        raise AuthError("Not signed in to ChatGPT. Sign in again in Settings.")
    body = {"grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": rt}   # Codex sends JSON here
    try:
        resp = http("POST", TOKEN_URL, headers={"Content-Type": "application/json"}, body=body, timeout=30)
    except HttpError as e:
        if e.status in (400, 401):
            raise AuthError("Your ChatGPT sign-in has expired or was revoked. Sign in again in Settings. (%s)" % _token_error(e))
        raise AuthError("ChatGPT could not refresh the sign-in (%d): %s" % (e.status, _token_error(e)))
    except ConnectionError as e:
        raise AuthError("Could not reach auth.openai.com: %s" % e)
    return _tokens_from_response(resp, previous=tokens)


def needs_refresh(tokens: Dict[str, Any], now: Optional[float] = None) -> bool:
    try:
        return float(tokens.get("expires_at", 0)) - (now if now is not None else time.time()) < REFRESH_MARGIN
    except (TypeError, ValueError):
        return True


# ---------------------------------------------------------------- storage (SecretStore slot "chatgpt")
def load_tokens(store) -> Dict[str, Any]:
    raw = store.get(STORE_KEY) if store is not None else ""
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) and d.get("access_token") else {}
    except Exception:
        return {}


def save_tokens(store, tokens: Dict[str, Any]) -> None:
    if store is not None:
        store.set(STORE_KEY, json.dumps(tokens))


def clear_tokens(store) -> None:
    if store is not None:
        store.set(STORE_KEY, "")


def status(store) -> Dict[str, Any]:
    """What the panel shows: signed in or not, and the plan name. Never the e-mail."""
    t = load_tokens(store)
    return {"signed_in": bool(t), "plan": plan_label(t.get("plan", "")) if t else ""}


# ---------------------------------------------------------------- the browser round trip
_SUCCESS_HTML = ("<!doctype html><meta charset='utf-8'><title>Pellaeon</title>"
                 "<body style='font-family:sans-serif;padding:2em'><h2>Signed in.</h2>"
                 "<p>You can close this tab and go back to ChimeraX.</p></body>")
_FAIL_HTML = ("<!doctype html><meta charset='utf-8'><title>Pellaeon</title>"
              "<body style='font-family:sans-serif;padding:2em'><h2>Sign-in did not complete.</h2><p>%s</p></body>")


class _Callback(BaseHTTPRequestHandler):
    flow: "LoginFlow"

    def do_GET(self):  # noqa: N802
        if not self.path.startswith(CALLBACK_PATH):
            self.send_error(404)
            return
        try:
            code = parse_callback(self.path, self.flow.state)
        except StateMismatch as e:
            self._reply(400, _FAIL_HTML % str(e).replace("<", "&lt;"))
            return                                    # not ours: keep waiting for the real reply
        except AuthError as e:
            self.flow._finish(error=e)
            self._reply(400, _FAIL_HTML % str(e).replace("<", "&lt;"))
            return
        self.flow._got_code(code)
        self._reply(200, _SUCCESS_HTML)

    def _reply(self, status: int, html: str):
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):   # never log the request line: it carries the code
        pass


class LoginFlow:
    """One sign-in attempt. start() binds the port and opens the browser; the outcome arrives on `on_done`.

    on_done(tokens, error): tokens is the dict to store (or None), error an AuthError (or None).
    """

    def __init__(self, on_done: Callable[[Optional[Dict[str, Any]], Optional[Exception]], None],
                 port: int = CALLBACK_PORT, timeout: float = LOGIN_TIMEOUT,
                 open_browser: Callable[[str], Any] = webbrowser.open, http: Http = request_json):
        self.on_done = on_done
        self.port = port
        self.timeout = timeout
        self.open_browser = open_browser
        self.http = http
        self.state = generate_state()
        self.pkce = generate_pkce()
        self.url = ""
        self._server: Optional[HTTPServer] = None
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._code = ""
        self._thread: Optional[threading.Thread] = None

    # ---- lifecycle ----
    def start(self) -> str:
        """Bind, open the browser, wait in a background thread. Returns the authorize URL (for a manual open)."""
        handler = type("Callback", (_Callback,), {"flow": self})
        try:
            self._server = HTTPServer(("127.0.0.1", self.port), handler)
        except OSError as e:
            raise PortBusy("Port %d on this computer is in use (another sign-in window, or the Codex CLI?). "
                           "Close it and try again. (%s)" % (self.port, e.strerror or e))
        self._server.timeout = 0.5
        bound_port = self._server.server_address[1]
        self.redirect = redirect_uri(bound_port)
        self.url = build_authorize_url(self.pkce["challenge"], self.state, self.redirect)
        self._thread = threading.Thread(target=self._serve, name="pellaeon-chatgpt-login", daemon=True)
        self._thread.start()
        try:
            self.open_browser(self.url)
        except Exception:   # the URL is still shown in the panel
            pass
        return self.url

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def finished(self) -> bool:
        return self._done.is_set()

    # ---- internals ----
    def _serve(self):
        deadline = time.time() + self.timeout
        try:
            while not self._done.is_set() and not self._cancel.is_set() and not self._code:
                if time.time() > deadline:
                    self._finish(error=AuthError("Sign-in timed out. Press Sign in with ChatGPT again."))
                    return
                self._server.handle_request()
            if self._code:
                self._exchange()
            elif self._cancel.is_set():
                self._finish(error=AuthError("Sign-in cancelled."))
        finally:
            try:
                self._server.server_close()
            except Exception:
                pass

    def _got_code(self, code: str):
        self._code = code

    def _exchange(self):
        try:
            tokens = exchange_code(self._code, self.pkce["verifier"], self.redirect, http=self.http)
        except AuthError as e:
            self._finish(error=e)
            return
        if not tokens.get("account_id"):
            self._finish(error=AuthError("Signed in, but the reply carried no ChatGPT account id. "
                                         "Pick a workspace in the browser and try again."))
            return
        self._finish(tokens=tokens)

    def _finish(self, tokens: Optional[Dict[str, Any]] = None, error: Optional[Exception] = None):
        with self._lock:
            if self._done.is_set():
                return
            self._done.set()
        try:
            self.on_done(tokens, error)
        except Exception:
            pass


def port_free(port: int = CALLBACK_PORT) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()
