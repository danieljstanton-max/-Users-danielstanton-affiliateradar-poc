# Affswap — project status

**What this is:** Affswap — a B2B network where verified iGaming affiliate
managers swap *connections* for casino / sportsbook / bingo / poker affiliate
sites (traffic data from DataForSEO). Python **standard library only** — no pip,
no Node.

## Live now (as of 2026-09-09)
- **Member app:** https://affswap.com  (also https://affswap.onrender.com)
- **Back office:** https://admin.affswap.com  — HTTP Basic Auth, user `admin`,
  password = the `ADMIN_PASS` set in the Render dashboard.
- **Host:** Render (free plan). Auto-deploys the `main` branch of this repo.
- **Domain:** affswap.com on GoDaddy → A `@` = `216.24.57.1`; CNAME `www` and
  `admin` → `affswap.onrender.com`.
- **Deploy state at last session:** DNS propagated ✓; HTTPS certificate was
  still issuing (Render / Let's Encrypt — completes within ~15 min of DNS).

## How it's served (`serve.py`)
One Python web service runs **both** apps sharing one SQLite DB. A front
reverse-proxy routes by hostname: any host starting `admin.` → back office
(Basic Auth via `ADMIN_USER`/`ADMIN_PASS`; if `ADMIN_PASS` is unset the back
office returns 503 so it's never open), everything else → member app. First boot
restores `seed/affiliateradar.db` onto Render's disk.

## Key files
- `serve.py` — production entrypoint (member app + back office on one port).
- `render.yaml` — Render blueprint (free plan; commented Starter+disk block for durable data).
- `seed/affiliateradar.db` — committed DB seed (~1,687 sites, 37 markets).
- `radar/` — the app (`app.py`), back office (`admin.py`), DB, discovery, swaps, reviews, alerts.
- `DEPLOY.md` — full deploy runbook (Render + GoDaddy).
- `affswap-suite.html` — the polished new UI (static prototype; phase-2 = wire onto live API).

## Run locally
```
PORT=8080 ADMIN_USER=admin ADMIN_PASS=<pick-one> python3 serve.py
```
→ http://localhost:8080 (member app). The back office needs an `admin.` host —
see `DEPLOY.md`.

## Secrets
`.env` is git-ignored (never committed). DataForSEO login/password live in `.env`
locally and in the Render dashboard. `ADMIN_PASS` is set in Render only.

## Working across machines (office ↔ home)
This GitHub repo is the source of truth. **`Pull` before you start, `Push` when
you finish** — on whichever machine. Render auto-deploys whatever lands on
`main`. Recreate `.env` on each machine only if you need to run live data pulls
locally (copy values from your password manager, never via chat).

## Next up (phase 2)
- Wire the polished suite UI (`affswap-suite.html`) onto the live `/api` + DB so
  the real product wears the new design.
- Make the GitHub repo **private** (currently public — exposes seed data).
- For durable data (survive restarts), upgrade Render to **Starter + persistent
  disk** (uncomment in `render.yaml`, set `AFFSWAP_DB_PATH`).
