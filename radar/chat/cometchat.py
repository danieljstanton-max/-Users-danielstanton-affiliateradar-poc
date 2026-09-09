"""Thin CometChat REST client — stdlib only, mock-first.

SECURITY: this runs on the BACKEND and uses the full-access REST API Key. That
key must never reach the mobile app. The client only ever receives a per-user
auth token minted by create_auth_token().

All REST paths/method names follow the reviewed spec but are best-effort against
CometChat's generation and marked `CONFIRM` — validate against the current SDK
major + Management API version before going live. In MOCK mode none of this
matters: calls are synthesised in-process and recorded for inspection.
"""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.request
import urllib.error

from .. import config


class CometChatError(RuntimeError):
    pass


class CometChatClient:
    def __init__(self):
        self.live = config.cometchat_is_live()
        self.app_id = config.COMETCHAT_APP_ID
        self.region = config.COMETCHAT_REGION
        self._key = config.COMETCHAT_REST_API_KEY
        # in MOCK mode we record every call we *would* have made, so the CLI /
        # back office can show exactly what hits CometChat when you go live.
        self.calls: list[str] = []

    # -- transport ----------------------------------------------------------
    def _base(self) -> str:
        # CONFIRM base-URL shape for your app/region in the dashboard.
        return f"https://{self.app_id}.api-{self.region}.cometchat.io/v3.0"

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        self.calls.append(f"{method} {path}")
        if not self.live:
            return self._mock(method, path, body)
        url = self._base() + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "apiKey": self._key,               # CONFIRM header name (apiKey vs appApiKey vs Authorization)
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            code = e.code
            payload = e.read().decode()[:300]
            # 409 = already exists → callers treat as success where noted
            raise CometChatError(f"HTTP {code} on {method} {path}: {payload}") from e

    def _mock(self, method: str, path: str, body: dict | None) -> dict:
        """Deterministic fake responses so the whole flow runs with no keys."""
        if path.endswith("/auth_tokens") and method == "POST":
            uid = path.split("/")[-2]
            token = "mocktok_" + hashlib.md5(uid.encode()).hexdigest()[:24]
            return {"data": {"authToken": token, "uid": uid}}
        if path.startswith("/messages/") and method == "GET":
            mid = path.split("/")[-1]
            return {"data": {"id": mid, "text": f"[mock message {mid} fetched server-side]"}}
        return {"data": {"ok": True, "mock": True}}

    # -- users --------------------------------------------------------------
    def ensure_user(self, uid: str, name: str, metadata: dict | None = None) -> dict:
        """Create the CometChat user; treat 'already exists' as success.
        CONFIRM: create semantics (upsert vs 409) and metadata size limits."""
        body = {"uid": uid, "name": name, "metadata": metadata or {}}
        try:
            return self._req("POST", "/users", body)
        except CometChatError as e:
            if "409" in str(e):                       # already exists → update display fields
                return self.update_user(uid, name, metadata)
            raise

    def update_user(self, uid: str, name: str, metadata: dict | None = None) -> dict:
        # Server OWNS name/metadata (anti-impersonation). CONFIRM: PUT path.
        return self._req("PUT", f"/users/{uid}", {"name": name, "metadata": metadata or {}})

    def deactivate_user(self, uid: str) -> dict:
        # CONFIRM: deactivate vs delete + `permanent` semantics; whether it drops live sockets.
        return self._req("PUT", f"/users/{uid}", {"deactivated": True})

    # -- auth tokens (the ONLY thing the client receives) -------------------
    def create_auth_token(self, uid: str, force: bool = True) -> str:
        """Mint a per-user auth token. `force` rotates prior tokens.
        CONFIRM: whether `force` exists / invalidates prior tokens, and response shape."""
        r = self._req("POST", f"/users/{uid}/auth_tokens", {"force": force})
        tok = (r.get("data") or {}).get("authToken")
        if not tok:
            raise CometChatError(f"no authToken in response: {r}")
        return tok

    def delete_auth_tokens(self, uid: str) -> dict:
        # Revocation on verification lapse. CONFIRM: delete-all path.
        return self._req("DELETE", f"/users/{uid}/auth_tokens")

    # -- groups (rooms) -----------------------------------------------------
    def create_group(self, guid: str, name: str, gtype: str = "private") -> dict:
        # CONFIRM: type strings (public/private/password).
        try:
            return self._req("POST", "/groups", {"guid": guid, "name": name, "type": gtype})
        except CometChatError as e:
            if "409" in str(e):
                return {"data": {"guid": guid, "exists": True}}
            raise

    def add_member(self, guid: str, uid: str) -> dict:
        # CONFIRM: body key (participants/members) + scope arrays.
        return self._req("POST", f"/groups/{guid}/members", {"participants": [uid]})

    def kick_member(self, guid: str, uid: str) -> dict:
        return self._req("DELETE", f"/groups/{guid}/members/{uid}")

    def ban_member(self, guid: str, uid: str) -> dict:
        # CONFIRM: dedicated /bans endpoint vs member body flag.
        return self._req("POST", f"/groups/{guid}/bannedMembers", {"participants": [uid]})

    # -- messages (moderation evidence + removal) ---------------------------
    def get_message(self, message_id: str) -> dict:
        # Server-side evidence fetch — never trust client-supplied text. CONFIRM path.
        return self._req("GET", f"/messages/{message_id}")

    def delete_message(self, message_id: str) -> dict:
        # CONFIRM: soft vs hard delete.
        return self._req("DELETE", f"/messages/{message_id}")
