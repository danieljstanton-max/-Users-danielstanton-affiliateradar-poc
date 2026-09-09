"""Community-chat (CometChat) backend integration for AffiliateRadar.

Split of responsibility:
  * CometChat owns the realtime messages + client UI (the mobile app).
  * WE (this backend) own the GATE: verification, the uid<->identity map,
    room provisioning, per-user auth-token minting, and the moderation queue.

Mock-first: everything runs against a fake CometChat in-process until you set
COMETCHAT_APP_ID / COMETCHAT_REGION / COMETCHAT_REST_API_KEY in .env — then the
exact same code calls the real REST API. Endpoints that must be validated against
current CometChat docs are marked `CONFIRM` (see docs/cometchat-integration.md).
"""
