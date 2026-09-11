"""Stdlib Stripe REST client — just enough for Checkout, Customer Portal
and webhook verification. No pip deps.

Env vars this reads:
    STRIPE_SECRET_KEY                 sk_test_... or sk_live_...
    STRIPE_WEBHOOK_SECRET             whsec_...        (webhook HMAC secret)
    STRIPE_PRICE_PRO                  price_...        (recurring)
    STRIPE_PRICE_UNLIMITED_EARLYBIRD  price_...        (recurring)
    STRIPE_PRICE_UNLIMITED_STANDARD   price_...        (recurring)
    STRIPE_PRICE_SWAP_TOPUP           price_...        (one-time, quantity-based)

Public entry points:
    configured()                                  -> bool
    price_id(plan_or_topup)                       -> str | None
    create_checkout(customer_id, mode, price_id,  -> dict {url, id, ...}
                    quantity, success_url,
                    cancel_url, metadata)
    create_portal(customer_id, return_url)        -> dict {url, ...}
    create_customer(email, name, manager_id)      -> dict {id, ...}
    verify_webhook(payload_bytes, sig_header)     -> dict (parsed event) | raises
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

_API_BASE = "https://api.stripe.com/v1"


def secret_key() -> str:
    return os.environ.get("STRIPE_SECRET_KEY", "")


def webhook_secret() -> str:
    return os.environ.get("STRIPE_WEBHOOK_SECRET", "")


def configured() -> bool:
    """True when we have enough to at least create a Checkout Session."""
    return bool(secret_key())


def price_id(kind: str) -> str | None:
    """Resolve a plan / product name to a Stripe Price ID from env."""
    return {
        "pro":                  os.environ.get("STRIPE_PRICE_PRO"),
        "unlimited_earlybird":  os.environ.get("STRIPE_PRICE_UNLIMITED_EARLYBIRD"),
        "unlimited_standard":   os.environ.get("STRIPE_PRICE_UNLIMITED_STANDARD"),
        "swap_topup":           os.environ.get("STRIPE_PRICE_SWAP_TOPUP"),
    }.get(kind)


# --- Low-level HTTP --------------------------------------------------------- #
def _post(path: str, form: dict) -> dict:
    """POST form-encoded data to the Stripe REST API. Stripe accepts
    x-www-form-urlencoded bodies with dotted-nested keys — no JSON."""
    if not secret_key():
        raise RuntimeError("stripe_not_configured")
    body = _encode_form(form)
    req = urllib.request.Request(
        _API_BASE + path,
        data=body.encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + secret_key(),
            "Content-Type": "application/x-www-form-urlencoded",
            "Stripe-Version": "2026-08-26.dahlia",
        })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        # Surface Stripe's error message for logging / debugging.
        try:
            payload = json.loads(e.read().decode() or "{}")
        except Exception:
            payload = {}
        msg = ((payload.get("error") or {}).get("message")
               or f"stripe_http_{e.code}")
        raise RuntimeError(msg) from e


def _encode_form(form: dict, prefix: str = "") -> str:
    """Stripe form encoding: nested dicts use dot notation, lists use
    bracket-index notation. Skips None/empty values.

        {"line_items": [{"price": "p_1", "quantity": 5}]}
    becomes:
        line_items[0][price]=p_1&line_items[0][quantity]=5
    """
    pairs: list[tuple[str, str]] = []

    def add(key: str, value):
        if value is None or value == "":
            return
        if isinstance(value, dict):
            for k, v in value.items():
                add(f"{key}[{k}]", v)
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                add(f"{key}[{i}]", v)
        elif isinstance(value, bool):
            pairs.append((key, "true" if value else "false"))
        else:
            pairs.append((key, str(value)))

    for k, v in form.items():
        add(f"{prefix}{k}" if prefix else k, v)
    return urllib.parse.urlencode(pairs)


# --- Public API ------------------------------------------------------------- #
def create_customer(email: str | None, name: str | None,
                    manager_id: int) -> dict:
    """Create a Stripe Customer for a member. `manager_id` is stored in
    the customer's metadata so webhooks can map back to a member row."""
    return _post("/customers", {
        "email": email or "",
        "name":  name or "",
        "metadata": {"manager_id": str(manager_id)},
    })


def create_checkout(customer_id: str | None, mode: str, price: str,
                    success_url: str, cancel_url: str,
                    quantity: int = 1, metadata: dict | None = None,
                    customer_email: str | None = None) -> dict:
    """Create a Checkout Session.
    mode = 'subscription' for recurring plans, 'payment' for one-time.
    Returns Stripe's session dict (we use `url` to redirect the user).

    Passing customer_id ties this to an existing customer (needed for
    the Customer Portal later). If we don't have one yet, pass
    customer_email so Stripe pre-fills the email field.
    """
    form: dict = {
        "mode": mode,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items": [{"price": price, "quantity": max(1, int(quantity))}],
        "allow_promotion_codes": True,
    }
    if customer_id:
        form["customer"] = customer_id
    elif customer_email:
        form["customer_email"] = customer_email
    if mode == "subscription":
        # Attach manager_id to the resulting subscription so webhook
        # handlers don't need a customer round-trip to find our row.
        if metadata:
            form["subscription_data"] = {"metadata": metadata}
    if metadata:
        form["metadata"] = metadata
    return _post("/checkout/sessions", form)


def create_portal(customer_id: str, return_url: str) -> dict:
    """Open the Stripe-hosted Customer Portal — members cancel /
    switch plan / update card there. Zero UI code on our side."""
    return _post("/billing_portal/sessions", {
        "customer": customer_id, "return_url": return_url,
    })


# --- Webhook verification --------------------------------------------------- #
class WebhookError(Exception):
    pass


def verify_webhook(payload: bytes, sig_header: str,
                   tolerance_seconds: int = 300) -> dict:
    """Verify a webhook signature and return the parsed event.
    Follows Stripe's docs exactly: https://stripe.com/docs/webhooks/signatures

    The Stripe-Signature header looks like:
        t=1697023452,v1=<hex sig>,v1=<older sig>,v0=<legacy>

    Any v1 that matches counts as valid.
    """
    if not webhook_secret():
        raise WebhookError("no_webhook_secret_configured")
    if not sig_header:
        raise WebhookError("missing_signature_header")
    parts = {}
    for pair in sig_header.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            parts.setdefault(k.strip(), []).append(v.strip())
    try:
        ts = int(parts.get("t", ["0"])[0])
    except ValueError:
        raise WebhookError("bad_timestamp")
    if abs(time.time() - ts) > tolerance_seconds:
        raise WebhookError("timestamp_out_of_range")
    signed = f"{ts}.".encode() + payload
    expected = hmac.new(webhook_secret().encode(), signed,
                        hashlib.sha256).hexdigest()
    signatures = parts.get("v1", [])
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise WebhookError("signature_mismatch")
    try:
        return json.loads(payload.decode() or "{}")
    except Exception as e:
        raise WebhookError("bad_payload_json") from e
