# Deploying Affswap live (Render + GoDaddy)

This puts the **real, DB-backed** Affswap live on your GoDaddy domain:

- `https://your-domain` → the **member app** (browse markets, sites, swaps — real data, real database).
- `https://admin.your-domain` → the **back office** (approve sites, review swaps, upload images) behind a password.

Both run from **one** Python service (`serve.py`) so they share one SQLite
database. No dependencies to install — pure standard library.

> **What I can and can't do for you:** all the code + config is ready and
> tested. The remaining steps need *your* logins (GitHub, Render, GoDaddy), so
> you run them — I can't enter your credentials. Each step below is copy‑paste.

---

## What's in the repo now (added for deploy)

| File | Purpose |
|---|---|
| `serve.py` | Production entrypoint. Runs member app + back office on one port; routes `admin.*` to the back office behind HTTP Basic Auth. |
| `render.yaml` | Render "Blueprint" — describes the service so Render sets it up automatically. |
| `requirements.txt` | Empty (stdlib only) — just tells Render this is a Python project. |
| `seed/affiliateradar.db` | Committed copy of the database (1.7 MB, 1,687 sites). Restored automatically on first boot. |
| `.gitignore` | Keeps `.env` (your secrets) **out** of the repo. |

---

## Step 1 — Put the code on GitHub

Render deploys from a Git repo. From the project folder:

```bash
cd /Users/danielstanton/affiliateradar-poc
git init
git add .
git commit -m "Affswap live: serve.py + Render blueprint"
```

Then create an **empty private repo** on github.com (call it `affswap`), and:

```bash
git branch -M main
git remote add origin https://github.com/<your-username>/affswap.git
git push -u origin main
```

Your `.env` (DataForSEO login/password) is git‑ignored, so no secrets are pushed.

---

## Step 2 — Create the service on Render

1. Go to **render.com** → sign up / log in (GitHub sign-in is easiest).
2. **New → Blueprint** → connect your GitHub → pick the `affswap` repo.
3. Render reads `render.yaml` and proposes an **affswap** web service on the
   **Free** plan. Click **Apply**.
4. When it asks for the values marked "set in dashboard", set:
   - **`ADMIN_PASS`** → a strong password (this protects the back office). Write it down.
   - **`ADMIN_HOSTNAME`** → `admin.your-domain` (e.g. `admin.affswap.io`).
   - `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` → only if you want the live
     server to refresh/discover new sites. Optional for now; leave blank.
5. First deploy takes ~1–2 min. You'll get a URL like `https://affswap.onrender.com`.
   Open it — the member app should load with real data. Add `-` nothing else needed.

**Test the back office before DNS is set:** it lives on the `admin.` hostname,
so locally you can't hit it yet by URL, but the app URL confirms the service is
up. Once DNS (Step 4) resolves, `https://admin.your-domain` will prompt for the
username `admin` and the `ADMIN_PASS` you set.

---

## Step 3 — Add your domains in Render

In the service → **Settings → Custom Domains → Add**, add three:

- `your-domain` (the apex, e.g. `affswap.io`)
- `www.your-domain`
- `admin.your-domain`

Render will show, next to each, the **exact DNS target** to use — a value like
`affswap.onrender.com` for the sub-domains and an **A-record IP** for the apex.
**Use the exact values Render shows you** (they can differ per account); the
table below is the shape of it.

---

## Step 4 — Point GoDaddy at Render

GoDaddy → your domain → **DNS → Manage Zones / DNS Records**. Add/edit:

| Type | Name | Value | Notes |
|---|---|---|---|
| A | `@` | *(the IP Render shows for the apex)* | the bare domain |
| CNAME | `www` | `affswap.onrender.com` | *(use Render's exact target)* |
| CNAME | `admin` | `affswap.onrender.com` | the back office |

Notes:
- GoDaddy can't CNAME the apex `@`; that's why the apex uses an **A record** to
  Render's IP. If you'd rather not, you can instead leave `@` as a **Domain
  Forward** to `https://www.your-domain` and rely on `www` — either works.
- Delete any old "Parked" A record GoDaddy added to `@`, or it'll clash.
- DNS propagation is usually minutes, occasionally up to an hour. Render then
  issues HTTPS certificates automatically (Let's Encrypt) — no action needed.

When it's live:
- `https://your-domain` → member app.
- `https://admin.your-domain` → login prompt → `admin` + your `ADMIN_PASS` → back office.

---

## Honest caveats (read these)

- **Free plan behaviour:** the service **sleeps after ~15 min idle**; the next
  visit cold-starts in ~30–60 s. And on any restart/redeploy the database
  **resets to the seed** — so swaps/reviews made while testing don't persist
  long-term. That's fine for "does it all work"; it is **not** durable storage.
- **To make data durable** (survive restarts, real sign-ups): switch to the
  **Starter** plan (~$7/mo) and attach a disk. In `render.yaml`, uncomment
  `plan: starter`, the `disk:` block, and the two `AFFSWAP_*` env lines, then
  redeploy. The DB then lives on `/var/data` and persists.
- **The back office is protected only by Basic Auth.** Use a strong
  `ADMIN_PASS`. It's meant for you/your staff, not the public.
- **Secrets never go in Git.** `ADMIN_PASS` and DataForSEO creds are set in the
  Render dashboard only.
- **Which UI is this?** This deploys the **database-backed member app** (the
  original `radar/app.py` interface) + the back office. The newer polished
  design (the "suite" artifact) is **phase 2** — we wire its buttons to this
  same live API/DB next, once the plumbing is confirmed working here.

---

## Run it locally first (optional sanity check)

```bash
cd /Users/danielstanton/affiliateradar-poc
PORT=8080 ADMIN_USER=admin ADMIN_PASS=choose-one python3 serve.py
```

- Member app: <http://localhost:8080>
- Back office: add a line `127.0.0.1 admin.localhost` to `/etc/hosts`, then
  <http://admin.localhost:8080> (login `admin` / your pass). Or on the server
  it's simply the `admin.` sub-domain.
