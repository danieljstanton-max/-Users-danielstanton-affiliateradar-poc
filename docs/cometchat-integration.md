# AffiliateRadar × CometChat — Engineer-Ready Integration Spec (adversarially reviewed & corrected)

## Key corrections (read first)

1. **"Short-lived auth tokens" is not a native CometChat feature — do not claim it.** CometChat auth tokens are long-lived by default and a user can hold several. The original spec was right to say "don't assume a TTL," but the security consequence was under-stated: **a still-valid token lets a user keep chatting even after they lose verification, until you actively revoke.** Real-time gating therefore depends entirely on *server-side revocation* (delete tokens + remove membership/deactivate + force-disconnect), not on token expiry. Rewritten in §2/§7.

2. **Biggest impersonation hole: client-side self-update of `name`/`metadata`.** The design renders the "verified ✓" badge and the `Sarah K · Casino · 7y` line from CometChat user `name`/`metadata`, which other members' clients read. If the CometChat client SDK lets a logged-in user update their **own** name/metadata (CONFIRM — historically possible via `updateUser`/`updateCurrentUserDetails`), a modified client could rename itself to impersonate another manager or forge sector/years. This vector was not identified in the original. Fixes in §4.

3. **Don't trust client-supplied `snapshotText` in reports.** A malicious reporter can fabricate the reported message text. The server must fetch the real message from CometChat by `messageId` (CONFIRM read endpoint) rather than storing attacker-controlled text as moderation evidence. Fixed in §5.1.

4. **`verified` in metadata is redundant and spoofable — prefer server-derived.** Since only verified users exist in CometChat at all, the badge should be authoritative from *our* backend/roster, not from a client-readable metadata flag. Keep metadata for display convenience only. §4.

5. **Auth token is itself a bearer secret on the device.** Original said "TLS only, never logged" — good, but add: store in Keychain/Keystore (secure storage), never in AsyncStorage/analytics/crash logs. §2.

6. **Current CometChat major version must be confirmed.** The original asserts "v4 is the current major line." CometChat has since shipped newer SDK/UIKit majors (UIKit v5 exists). **Do not hard-code v4 assumptions** — confirm the current Chat SDK major version and its Management REST API version against the live dashboard before coding. Every method/endpoint below stays marked CONFIRM.

7. **Public-group fallback (design B) is weaker than stated and should be dropped for this product.** With public groups, gating exists only at token-issuance time; combined with non-expiring tokens, a lapsed-verification user retains access until revoked, and any logged-in app user can wander in. Use **private groups + server-managed membership (design A)** as the single design. §3.

8. **Apple 1.2 has a prong the spec omitted: a published/in-app point of contact** and the report control must be reachable **on the offending content itself**. Added in §5.

9. **Pseudonymization ≠ anonymization under GDPR.** CometChat still holds personal data (re-identifiable via your map + free-text messages), so the DPA/sub-processor/residency obligations fully apply — the original implied minimization softens this more than it does. §6.

Everything marked **`CONFIRM IN DOCS`** must be validated against current CometChat docs/dashboard. Do not treat any literal path, method, header, or param below as authoritative over the live docs. Where I was unsure I flagged it rather than inventing a replacement.

---

## 0. Core principle

Two credentials, very different blast radii:

| Credential | Where it lives | What it can do |
|---|---|---|
| **REST API Key** (full-access) | **Server ONLY** — secrets manager / env var. Never in the bundle, never sent to a client. | God-mode: create/delete users, mint/revoke auth tokens, create groups, add/kick/ban members, delete messages, read everything. |
| **Auth Token** (per-user) | Minted server-side, handed to one client, used to `login()`. **Bearer secret** on device → secure storage only. | Authenticate as exactly one UID. Long-lived by default (see §2) — treat as sensitive until revoked. |
| **Auth Key** (auth-only scope) | Prefer **not shipping it at all** (§1). If present, only enables `login(UID, authKey)`. | Logs a user in by UID with **no server check** — the exact self-authentication we forbid. |

**Invariants:** the client never holds the REST API Key; the client never self-authenticates (no `login(UID, authKey)`); the client receives only a freshly minted **Auth Token**, and only after our backend confirmed VERIFIED.

---

## 1. Accounts / keys / regions

From the CometChat dashboard:

- **App ID** — client-safe identifier. Ships in the app.
- **Region** — `us` / `eu` / `in`. Pick **`eu`** (§6). Client-safe.
- **Auth Key** — scoped key (Auth-Only vs Full-Access). **`CONFIRM IN DOCS`** the exact scope labels in the current dashboard. **We intend not to ship it** (see below).
- **REST API Key** — full-access, **server-only**.

**Client init needs App ID + Region + AppSettings only.** The Auth Key is consumed only by the `login(UID, authKey)` path, which we do not use. **`CONFIRM IN DOCS` that current `CometChat.init()` / `AppSettingsBuilder` does not require an Auth Key** so we can keep it out of the bundle entirely. If a current SDK version *does* require it at init, that changes the threat model — treat that as a blocker to resolve, not an assumption.

```
# Client (safe to ship)
COMETCHAT_APP_ID = "xxxxxxxxxxxx"
COMETCHAT_REGION = "eu"

# Server (secret)
COMETCHAT_REST_API_KEY = "••••"   # full-access
```

Add a **CI guard** that fails the build if the REST key env-var name or value pattern appears anywhere in the mobile bundle/source map.

---

## 2. The VERIFIED-gating flow (end to end)

Client talks only to (a) our backend and (b) the CometChat **Chat SDK** using a token our backend minted. Client never touches the CometChat REST API.

```
RN app                         AffiliateRadar backend                 CometChat REST
  │  POST /chat/session (our JWT)      │                                   │
  ├───────────────────────────────────▶                                   │
  │                          1. AuthZ our JWT                              │
  │                          2. verified == true? (LinkedIn OAuth done +   │
  │                             work-email confirmed + status VERIFIED)    │
  │                             else → 403 NOT_VERIFIED                     │
  │                          3. Ensure CC user (server-owned name/meta) ───▶ POST /v3.0/users
  │                          4. (design A) ensure group membership ────────▶ POST /v3.0/groups/{g}/members
  │                          5. Mint per-user auth token (force rotate) ───▶ POST /v3.0/users/{uid}/auth_tokens
  │                          ◀──────────────────────────────────────────── { authToken }
  │  200 { appId, region, uid, authToken, allowedGroups[] }                │
  ◀───────────────────────────────────┤                                   │
  │  CometChat.login(authToken)   (Chat SDK, direct)                       │
  ├──────────────────────────────────────────────────────────────────────▶
```

### Step 2 — the gate (our server, authoritative)
Our DB is the **only** source of truth for "may this person chat." CometChat never decides it.

```python
def issue_chat_session(current_user):
    m = db.get_manager(current_user.id)
    if not (m.linkedin_verified and m.work_email_confirmed and m.status == "VERIFIED"):
        raise HTTP403("NOT_VERIFIED")
    uid = cc_uid(m)                       # §4 — opaque, from our map
    ensure_cometchat_user(m, uid)         # step 3, server-owns name/metadata
    ensure_memberships(m, uid)            # step 4, design A
    token = create_auth_token(uid, force=True)   # step 5, rotate previous
    return {"appId": APP_ID, "region": REGION, "uid": uid,
            "authToken": token, "allowedGroups": groups_for(m)}
```

Rate-limit this endpoint per user/IP; it both mints credentials and re-runs the gate.

### Step 3 — ensure the CometChat user (server owns identity fields)
```
POST https://{appId}.api-{region}.cometchat.io/v3.0/users
Headers: apiKey: {REST_API_KEY}     # CONFIRM header name (apiKey vs appApiKey vs Authorization)
Body:   { "uid": "mgr_8f21c", "name": "Sarah K",
          "metadata": { "sector": "Casino", "years": 7 } }
```
On later sessions, if display fields changed, `PUT /v3.0/users/{uid}`. **`CONFIRM IN DOCS`:** create semantics (upsert vs catch-409), update path, and metadata size limits. Do **not** store `verified`, email, legal name, or LinkedIn URL here (§4).

### Step 5 — mint the per-user Auth Token (server-side, REST key)
```
POST https://{appId}.api-{region}.cometchat.io/v3.0/users/{uid}/auth_tokens
Headers: apiKey: {REST_API_KEY}
Body:   { "force": true }     # CONFIRM: whether force exists and whether it invalidates prior tokens
Resp:   { "data": { "authToken": "…", "uid": "mgr_8f21c" } }   # CONFIRM shape
```
Return only `authToken` (+ appId/region/uid/allowedGroups). Never return the REST key or Auth Key.

**On "short-lived":** CometChat does not give you a server-set TTL you can rely on — **`CONFIRM IN DOCS`.** So we do not claim tokens are short-lived. Instead:
- **Rotate on every session** (`force: true`) so at most one live token per device is current, and re-mint re-runs the gate.
- **Revoke on any status change** (§7) — this, not expiry, is what enforces verification in near-real-time.
- Client stores the token in **Keychain/Keystore**, never logs it, never puts it in analytics/crash reports, TLS only.

### Client login + join
```js
await CometChat.init(APP_ID, new CometChat.AppSettingsBuilder()
    .subscribePresenceForAllUsers()     // CONFIRM builder methods for current SDK major
    .setRegion(REGION).build());

const user = await CometChat.login(session.authToken);   // token path ONLY — no UID+authKey

// Design A: server already added membership; client may still need joinGroup for private
for (const guid of session.allowedGroups) {
  try { await CometChat.joinGroup(guid /*, type, password */); }   // CONFIRM signature
  catch (e) { if (e.code !== "ALREADY_JOINED") throw e; }          // CONFIRM error code string
}
```
Non-verified users never reach this — `/chat/session` returned 403 and the app shows the **"verify to join"** locked state.

---

## 3. Group / room model

Seven mocked rooms → CometChat **Groups**, one stable GUID each:

| Room | GUID |
|---|---|
| Affiliate Lounge | `room_lounge` |
| Casino | `room_casino` |
| Sportsbook | `room_sportsbook` |
| Bingo | `room_bingo` |
| Poker | `room_poker` |
| Deals & Partnerships | `room_deals` |
| New to Affiliates | `room_new` |

**Use `private` groups with server-managed membership (design A). Drop the public-group fallback (design B).** Rationale: with non-expiring tokens, a public-group design keeps a lapsed-verification user in rooms until you revoke, and any logged-in app user can self-join. Private + server membership makes every join an auditable server decision and gives defense-in-depth against a leaked token.

```
POST /v3.0/groups
  { "guid":"room_casino", "name":"Casino", "type":"private" }   # CONFIRM type strings

POST /v3.0/groups/{guid}/members
  { "participants": ["mgr_8f21c"] }   # CONFIRM key name (participants/members) & role arrays
```

**Roles / scopes** (historically `admin` / `moderator` / `participant` — **`CONFIRM IN DOCS`** exact strings):
- Every verified manager → `participant`.
- Optional trusted community mods → `moderator`.
- **Operator/back-office actions run server-side with the REST key** — no one needs admin in the client.

---

## 4. Semi-anonymous identity mapping

**Golden rule:** CometChat holds only the pseudonymous view. Real identity (legal name, LinkedIn URL, work email) stays in our DB, keyed by our internal manager ID. **Note under GDPR this is pseudonymization, not anonymization — CometChat data is still personal data (§6).**

| CometChat field | Value | Notes |
|---|---|---|
| `uid` | opaque, stable, non-reversible, e.g. `mgr_8f21c` | Never derive from email/name/LinkedIn. `uid ↔ real manager` map lives only in our DB. |
| `name` | server-assigned handle, e.g. `Sarah K` | **Server-owned.** Must be unique enough to resist impersonation — append a stable discriminator if collisions are possible. |
| `metadata` | `{ "sector":"Casino", "years":7 }` | Display convenience only; treat as **public to the room**. |
| avatar | optional initials/generic | Never a real LinkedIn photo unless the manager opts in. |

**Impersonation & badge-spoofing fixes (new):**
- **`CONFIRM IN DOCS` whether a logged-in client can update its own `name`/`metadata`** (e.g. `updateCurrentUserDetails`/`updateUser`). If it can, a modified client could rename itself to impersonate another manager or forge sector/years, and other members' clients would render the forgery.
  - Mitigation regardless of the answer: **treat `name`/`metadata` as server-owned.** On every `/chat/session`, reconcile CometChat profile fields to our DB values (overwrite via REST). Optionally subscribe to a CometChat **webhook** on profile change to correct drift, or reconcile on a schedule.
  - **Do not store `verified` in CometChat metadata.** The badge is authoritative from *our* backend/roster, not a client-readable flag. Render `✓` from server-verified data your app already trusts, reinforced by the fact that only verified users exist in CometChat at all.
- Accountability: operator resolves `uid → real identity` in our back office; that map never leaves our DB.

**`CONFIRM IN DOCS`:** metadata size limits and exactly which user fields are returned to other members' clients (assume all of `name`/`metadata` is public to the room).

---

## 5. Moderation (Apple Guideline 1.2)

Apple 1.2 for UGC requires: **(a) EULA/terms with zero tolerance for objectionable content, (b) a method to filter objectionable content, (c) a report mechanism reachable on the content itself, (d) a way to block abusive users, (e) act on reports and eject offenders (expected within 24h), and (f) a published/in-app point of contact.** Coverage:

### 5.1 Report message → existing back-office queue
Client "Report" (reachable directly on the message) POSTs to **our** backend, not CometChat:
```
POST /chat/reports (our JWT)
  { "guid":"room_casino", "messageId":"…", "reportedUid":"mgr_9a…", "reason":"harassment" }
```
**Server fetches the real message content from CometChat by `messageId`** (`CONFIRM` read endpoint, e.g. `GET /v3.0/messages/{id}`) — **do not trust any client-supplied snapshot text as evidence.** Backend resolves `reportedUid → real identity` (server-side only) and writes a queue item into the **same moderation surface** used for candidate-affiliate approvals and review moderation. Operators triage there. This satisfies the report + act-within-24h prongs with a human workflow we already run.

`CONFIRM IN DOCS`: CometChat also has native message flagging/reporting and a Moderation capability; you may additionally wire those, but the **queue of record is ours** (for identity resolution and consistency).

### 5.2 Block user (personal)
`CometChat.blockUsers([uid])` / `unblockUsers` — **`CONFIRM IN DOCS`** current method names. Personal, client-side; stops one manager seeing another. Does not remove from rooms.

### 5.3 Operator kick / ban / remove message (server-side, REST key)
```
DELETE /v3.0/groups/{guid}/members/{uid}          # kick — CONFIRM path
POST   /v3.0/groups/{guid}/members {...}           # ban — CONFIRM shape / dedicated /bans endpoint
DELETE /v3.0/messages/{messageId}                  # remove message — CONFIRM path & soft vs hard
```
Global ban = deactivate the CometChat user + DB status=banned + delete tokens (§7).

### 5.4 Automated filtering (extensions)
`CONFIRM IN DOCS` current names/availability: **Profanity Filter**, **Data Masking** (masks emails/phones/cards — useful in deal chat), optional Image Moderation / Sentiment. Profanity + Data Masking give Apple the "filter objectionable content" prong; the report queue gives "flag + act."

### 5.5 Terms + contact
- Ship a chat EULA / community guidelines with explicit zero-tolerance terms; require acceptance before first chat entry; **record acceptance (timestamp + version) in our DB.**
- **Provide an in-app and published point of contact** for abuse reports/appeals (App Store metadata + in-app). Required by 1.2 and easy to miss.

---

## 6. Data / GDPR (Gibraltar regime)

- **Region:** provision in **EU** (`eu`). `CONFIRM IN DOCS` that EU residency covers message content, attachments, **and backups**, and that CometChat's sub-processor list + **signed DPA** cover a Gibraltar/EU-equivalent posture.
- **This is pseudonymization, not anonymization.** CometChat still holds personal data: the pseudonymous profile is re-identifiable via our map, and free-text messages may contain personal data. So DPA/residency/lawful-basis obligations apply in full — the re-identification map living in our DB reduces linkage but does not exempt CometChat data from GDPR.
- **Lawful basis:** define it (likely legitimate interest for a B2B professional network) and document it.
- **Retention:** define a concrete window (e.g. 12 months). `CONFIRM IN DOCS` whether CometChat offers configurable auto-purge; if not, run a scheduled server job deleting messages older than the window via REST (`CONFIRM` message/conversation purge endpoint). Reported-message evidence follows our own moderation-record retention.
- **Right to access / export (DSAR):** backend assembles the user's data, **including CometChat-held messages** (`CONFIRM` export/read endpoints).
- **Right to erasure:** on deletion — (1) deactivate/delete the CometChat user, (2) purge messages per policy, (3) delete the `uid ↔ identity` map row, (4) delete auth tokens. Document the tension between erasing messages and preserving moderation evidence (e.g. retain reported-message snapshots under bounded legitimate-interest, purge the rest).

---

## 7. Failure & edge cases

| Case | Handling |
|---|---|
| **Auth token invalid at `login()`** | Client catches → re-calls `/chat/session` for a fresh token → retry `login()` once, no loop. `CONFIRM` the invalid/expired error code to distinguish from offline. |
| **Refresh cadence** | No reliable server TTL — refresh by re-calling `/chat/session` on cold start, login failure, and after re-verification. Each call re-runs the gate. |
| **Verification lapses / revoked** | DB status ≠ VERIFIED, then **actively revoke** (expiry won't save you): (1) delete the user's auth token(s) — `CONFIRM` delete endpoint; (2) remove from private groups **and/or** deactivate the CometChat user; (3) next `/chat/session` → 403 → locked state. **`CONFIRM` whether token deletion force-disconnects a live socket** — assume not, so also remove-from-group/deactivate to drop live sessions. |
| **User banned (global)** | Deactivate CometChat user (`CONFIRM` deactivate vs delete + permanent flag) + delete tokens + DB status=banned. App shows "suspended," not "verify." |
| **Kicked/banned from one room** | Server REST kick/ban; client group-event listener updates UI. `CONFIRM` event names (e.g. `onGroupMemberKicked`). |
| **`joinGroup` already a member** | Treat as success (`CONFIRM` ALREADY_JOINED code). |
| **CometChat down** | Chat is a retention feature — fail soft ("chat unavailable, retry"), never block the rest of the app. |
| **Duplicate user create (race)** | Idempotent ensure-user: catch 409/"already exists," proceed to token mint. |
| **Client obtains REST key** | Impossible by design; CI guard (§1) fails the build if the key appears in the bundle. |
| **Self-profile drift/impersonation** | Reconcile `name`/`metadata` to DB on each session and/or via profile-change webhook (§4). |

---

## Confirm in CometChat docs (checklist)

1. **Current Chat SDK major version** and Management REST API version (do not assume v4; UIKit v5+ exists). Every method/endpoint below is version-sensitive.
2. REST base-URL shape `https://{appId}.api-{region}.cometchat.io/v3.0/...` and **auth header name** (`apiKey` vs `appApiKey` vs `Authorization`).
3. `CometChat.init()` / `AppSettingsBuilder` signature — **and that no Auth Key is required** for token-based login (blocker if it is).
4. `POST /users` create semantics (upsert vs 409); `PUT /users/{uid}` update; **metadata size limits and which user fields are visible to other members.**
5. **Whether a logged-in client can update its own `name`/`metadata`** (impersonation vector, §4) and the exact method name if so.
6. `POST /users/{uid}/auth_tokens` request/response shape, whether `force` exists and invalidates prior tokens, **token TTL & multiplicity**, and the **token-deletion** endpoint.
7. **Whether deleting a token or deactivating a user force-disconnects live SDK sessions** (drives §7 revocation design).
8. Group create `type` strings (public/private/password), scope strings (`admin`/`moderator`/`participant`), add/kick/**ban** member endpoint bodies (`participants`/`bannedMembers` keys, dedicated `/bans` path?).
9. **Read a single message by ID** (server-side report evidence, §5.1) and **delete message** endpoint (soft vs hard) + bulk/retention purge capability.
10. Client SDK methods/events: `login`, `joinGroup` signature, `blockUsers`/`unblockUsers`, `ALREADY_JOINED` / token-expired error codes, `onGroupMemberKicked`/`onGroupMemberBanned` listener names.
11. Deactivate vs delete user (`permanent` flag semantics).
12. Moderation extensions currently offered (Profanity Filter, Data Masking, Image Moderation, native Reporting/Moderation) and whether native reporting can webhook into our queue.
13. **EU data residency** scope (messages, attachments, backups), **sub-processor list + DPA** for Gibraltar-equivalent compliance, and any built-in retention/auto-purge controls.
14. **Webhooks** available (message-sent, moderation-flagged, user-profile-changed) for archival, auto-moderation routing, and profile-drift reconciliation.

---

### One-paragraph summary
Our backend is the single gate: every `/chat/session` call re-verifies the manager (LinkedIn + work email) in our DB, reconciles a pseudonymous CometChat user whose `name`/`metadata` we own, ensures private-group membership, and mints a rotated per-user auth token with the server-only REST API Key. The client receives only that token and calls `CometChat.login(token)`. Because CometChat tokens are not reliably short-lived, near-real-time gating depends on **active server-side revocation** (delete tokens + remove membership/deactivate + force-disconnect) the moment verification lapses — not on expiry. Real identity never leaves our DB; the "verified ✓" badge is authoritative from our backend, not a spoofable metadata flag; reports carry server-fetched evidence into our existing moderation queue (satisfying Apple 1.2 alongside block, operator kick/ban, profanity/data-masking, terms, and a published contact); and the app runs in the EU region under a signed DPA with a defined retention/export/erasure story. Every literal CometChat path/method is marked CONFIRM IN DOCS and must be validated against the current SDK major version before implementation.