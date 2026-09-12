"""Resend HTTP client — stdlib only. Sends transactional email.

Env vars:
    RESEND_API_KEY          re_...              required
    ALERTS_FROM_EMAIL       alerts@affswap.com  from address (default onboarding@resend.dev)
    ALERTS_FROM_NAME        Affswap             from display name
    PUBLIC_URL              https://www.affswap.com  base for unsubscribe links etc.

Public entry point:
    configured()  -> bool
    send(to, subject, html, text=None, tag=None) -> dict  raises on network/API error
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

RESEND_URL = "https://api.resend.com/emails"


def api_key() -> str:
    return os.environ.get("RESEND_API_KEY", "")


def from_email() -> str:
    return (os.environ.get("ALERTS_FROM_EMAIL")
            or "onboarding@resend.dev").strip()


def from_name() -> str:
    return (os.environ.get("ALERTS_FROM_NAME") or "Affswap").strip()


def configured() -> bool:
    return bool(api_key())


def public_url() -> str:
    return (os.environ.get("PUBLIC_URL")
            or "https://www.affswap.com").rstrip("/")


class EmailError(RuntimeError):
    pass


def send(to: str, subject: str, html: str,
         text: str | None = None, tag: str | None = None) -> dict:
    """Send one email via Resend. Raises EmailError on failure."""
    if not configured():
        raise EmailError("resend_not_configured")
    if not to or "@" not in to:
        raise EmailError("bad_recipient")
    payload = {
        "from": f"{from_name()} <{from_email()}>",
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    if tag:
        payload["tags"] = [{"name": "kind", "value": tag}]
    req = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key(),
            "Content-Type": "application/json",
            # Cloudflare (which fronts Resend) blocks the default
            # 'Python-urllib' UA with error 1010, so send our own.
            "User-Agent": "Affswap/1.0 (+https://www.affswap.com)",
            "Accept": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()
        except Exception:
            pass
        raise EmailError(f"resend_{e.code}: {body[:400]}") from e
    except Exception as e:
        raise EmailError(f"resend_network: {e}") from e
