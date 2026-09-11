"""AffiliateRadar PoC command line.

    python3 -m radar initdb --reset
    python3 -m radar discover --all
    python3 -m radar refresh --build-history
    python3 -m radar screenshot --all
    python3 -m radar show country --country GB --vertical casino
    python3 -m radar show profile --domain casinopilot-uk.example --contact
    python3 -m radar prove-ownership
    python3 -m radar demo            # runs the whole thing end to end
"""
from __future__ import annotations

import argparse
import json
import sys

from . import config, keywords, ownership
from .db import connect, init_db, now_iso
from .discovery import discover
from .refresh import refresh_all, HISTORY_WEEKS, LATEST_WEEK
from .providers.dataforseo import DataForSEOClient
from .providers.screenshot import ScreenshotClient
from . import views

BAR = "─" * 68


def _banner(title: str) -> None:
    print(f"\n{BAR}\n{title}\n{BAR}")


# --------------------------------------------------------------------------
def cmd_initdb(args) -> None:
    init_db(reset=args.reset)
    print(f"DB ready at {config.DB_PATH}  ({'reset' if args.reset else 'kept'})")
    print(config.mode_banner())


def cmd_discover(args) -> None:
    conn = connect()
    try:
        if args.world:
            pairs = keywords.world_markets(args.vertical)
        elif args.all:
            pairs = keywords.markets()
        else:
            pairs = [(args.country, args.vertical)]
        _banner("DISCOVERY — SERP → dedup domains → candidates")
        for country, vertical in pairs:
            r = discover(conn, country, vertical)
            print(f"  {r['country']:>3} / {r['vertical']:<10} "
                  f"keywords={r['keywords']}  domains={r['domains']}  "
                  f"new_candidates={r['new_candidates']}  hits={r['hits']}")
    finally:
        conn.close()


def cmd_refresh(args) -> None:
    from .providers.dataforseo import DataForSEOError
    conn = connect()
    try:
        _banner("REFRESH — Domain Rank Overview → snapshot → trend (API-owned only)")
        weeks = range(HISTORY_WEEKS) if args.build_history else [args.week]
        for w in weeks:
            try:
                r = refresh_all(conn, week_index=w)
            except DataForSEOError as e:
                print(f"  ✗ DataForSEO refresh stopped: {e}")
                print("    Nothing was charged or written. Common causes:")
                print("      · 402 Payment Required → add funds in the DataForSEO dashboard (Billing)")
                print("      · 401 → check DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD in .env")
                print("    Fix that, then re-run `python3 -m radar refresh`.")
                return
            print(f"  week {w} ({r['iso_week']}):  sites_seen={r['sites_seen']}  "
                  f"snapshots={r['snapshots']}  api_updates={r['updated']}")
    finally:
        conn.close()


def cmd_history(args) -> None:
    from .refresh import build_history
    from .providers.dataforseo import DataForSEOError
    conn = connect()
    try:
        _banner("HISTORY — real monthly traffic (Historical Bulk Traffic Estimation)")
        try:
            r = build_history(conn, months=args.months,
                              progress=lambda n, t, c, cached: print(
                                  f"  market {n:>2}/{t}  (loc {c}){'  [cached]' if cached else ''}"))
        except DataForSEOError as e:
            print(f"  ✗ stopped (resumable — cached markets kept): {e}")
            return
        print(f"\n  window={r.get('window')}  sites={r['sites_seen']}  "
              f"months={r['months']}  snapshots={r['snapshots']}  updated={r['updated']}  "
              f"markets={r.get('markets_done')}/{r.get('markets_total')}")
    finally:
        conn.close()


def cmd_screenshot(args) -> None:
    conn = connect()
    client = ScreenshotClient()
    try:
        _banner("SCREENSHOTS — homepage capture (API-owned, low cadence)")
        if args.all:
            # never re-capture a site whose screenshot was uploaded by hand
            where = ("WHERE etv > 0 AND id NOT IN "
                     "(SELECT site_id FROM screenshots WHERE provider='manual')")
            if args.missing:
                where += " AND id NOT IN (SELECT site_id FROM screenshots)"
            q = f"SELECT id, domain FROM sites {where} ORDER BY (etv IS NULL), etv DESC"
            if args.limit:
                q += f" LIMIT {int(args.limit)}"
            rows = conn.execute(q).fetchall()
        else:
            rows = conn.execute("SELECT id, domain FROM sites WHERE domain = ?",
                                (args.domain,)).fetchall()
        print(f"  provider: {client.provider}  ·  capturing {len(rows)} site(s)\n")
        for r in rows:
            res = client.capture(f"https://{r['domain']}/", r["domain"])
            conn.execute(
                """INSERT INTO screenshots (site_id, source_url, image_ref, provider, captured_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(site_id) DO UPDATE SET
                     image_ref=excluded.image_ref, provider=excluded.provider,
                     captured_at=excluded.captured_at""",
                (r["id"], f"https://{r['domain']}/", res["image_ref"],
                 res["provider"], now_iso()),
            )
            print(f"  {r['domain']:<32} → {res['provider']:<12} {res['image_ref']}")
        conn.commit()
    finally:
        conn.close()


def cmd_show(args) -> None:
    conn = connect()
    try:
        if args.what in ("home", "countries"):
            data = views.countries_index(conn)
            _banner(f"HOME — browse by market ({data['markets']} countries)")
            for c in data["countries"]:
                etv = f"{int(c['total_etv']):>10,}" if c["total_etv"] else "         —"
                bl = f"  ⚠ {c['blacklisted']}" if c["blacklisted"] else ""
                print(f"  {c['flag']} {c['name']:<18} {c['count']:>3} affiliates   {etv}/mo{bl}")
            if args.json:
                print("\nJSON:\n" + json.dumps(data, indent=2, ensure_ascii=False))
        elif args.what == "blacklist":
            data = views.blacklist(conn)
            _banner(f"⚠ BLACKLIST — {data['count']} flagged sites")
            for e in data["entries"]:
                who = f" ({e['company_name']})" if e["company_name"] else ""
                print(f"  {e['origin']['flag']} {e['name']}{who}")
                print(f"      ↳ {e['reason']}")
            if args.json:
                print("\nJSON:\n" + json.dumps(data, indent=2, ensure_ascii=False))
        elif args.what == "movers":
            data = views.market_movers(conn, args.country, vertical=args.vertical)
            c = data["country"]
            _banner(f"MARKET MOVERS — {c['flag']} {c['name']}"
                    + (f" · {args.vertical}" if args.vertical else "")
                    + f" · ~90-day change ({data['sample']} affiliates)")
            print("  ▲ BIGGEST RISERS")
            for m in data["risers"]:
                print(f"     {m['origin']['flag']} {m['name']:<26} "
                      f"{int(m['etv']):>8,}/mo   +{m['pct']}%")
            print("  ▼ BIGGEST DECLINES")
            for m in data["fallers"]:
                print(f"     {m['origin']['flag']} {m['name']:<26} "
                      f"{int(m['etv']):>8,}/mo   {m['pct']}%")
            if args.json:
                print("\nJSON:\n" + json.dumps(data, indent=2, ensure_ascii=False))
        elif args.what == "country":
            sort = getattr(args, "sort", "traffic")
            data = views.country_list(conn, args.country, args.vertical, sort=sort)
            c = data["country"]
            _banner(f"COUNTRY — {c['flag']} {c['name']}"
                    + (f" · {args.vertical}" if args.vertical else "")
                    + f"  · sort: {sort}  ({data['count']})")
            for card in data["cards"]:
                if card.get("blacklisted"):
                    who = f" ({card['company_name']})" if card.get("company_name") else ""
                    print(f"  ⚠ {card['name']}{who}")
                    print(f"      ↳ {card['reason']}")
                    continue
                etv = f"{int(card['etv']):>8,}" if card["etv"] else "     —  "
                newb = " [NEW]" if card["is_new"] else ""
                if sort == "reviews":
                    rating = f"★{card['rating']:.1f} ({card['review_count']})" if card["rating"] else "☆ no reviews"
                    print(f"  {card['traffic_origin']['flag']} {card['name']:<26}"
                          f"{rating:>16}   {etv}/mo{newb}")
                else:
                    lock = "☎" if card["has_contact"] else " "
                    print(f"  {card['traffic_origin']['flag']} {card['name']:<26}"
                          f"{etv}/mo  {card['trend']:>10}  {lock}  "
                          f"{'/'.join(card['verticals'])}{newb}")
            if args.json:
                print("\nJSON:\n" + json.dumps(data, indent=2, ensure_ascii=False))
        else:  # profile
            data = views.site_profile(conn, args.domain, include_contact=args.contact)
            if not data:
                print(f"no such site: {args.domain}"); return
            _banner(f"SITE PROFILE — {data['name']}  ({data['domain']})")
            print(f"  classification : {data['classification']} (source={data['source']})")
            print(f"  traffic (etv)  : {data['etv']}  {data['traffic_origin']['flag']} "
                  f"{data['traffic_origin']['iso']}   trend {data['trend']}")
            if data.get("regions"):
                mk = "  ".join(f"{r['flag']} {r['iso']} {int(r['etv']):,}"
                               + ("*" if r["primary"] else "") for r in data["regions"])
                print(f"  markets        : {mk}   (* primary · files under each)")
            print(f"  verticals      : {', '.join(data['verticals'])}")
            print(f"  screenshot     : {data['screenshot']}")
            print(f"  history        :")
            for h in data["traffic_history"]:
                print(f"      {h['week']}   etv={h['etv']}   top={h['top_country']}")
            if data["contact_locked"]:
                print("  contact        : 🔒 locked (verify to unlock)")
            elif not data["contact"]:
                print("  contact        : (none on file — add in back office)")
            else:
                ch = data["contact"]["channels"]
                print(f"  company        : {data['contact']['company_name']}")
                for name, key in (("email", "email"), ("telegram", "telegram"), ("teams", "teams")):
                    val = ch[key]
                    mark = "✓" if val else "·"
                    print(f"  {name:<8} {mark}     : {val or 'not listed'}")
            if args.json:
                print("\nJSON:\n" + json.dumps(data, indent=2, ensure_ascii=False))
    finally:
        conn.close()


# --------------------------------------------------------------------------
def cmd_prove_ownership(args) -> None:
    """The headline demo: an API refresh cannot overwrite human-owned data."""
    conn = connect()
    try:
        _banner("PROVE OWNERSHIP — refresh must never wipe human data")

        # ensure we have data to work with
        if not conn.execute("SELECT 1 FROM sites LIMIT 1").fetchone():
            discover(conn, "GB", "casino")
        refresh_all(conn, week_index=0)  # baseline history

        domain = "casinopilot-uk.example"
        site = conn.execute("SELECT id FROM sites WHERE domain = ?",
                            (domain,)).fetchone()
        site_id = site["id"]

        # a MANUAL-only site: uploaded by a human, no SERP/API presence at all.
        # It must survive every refresh with its contact intact.
        from .db import upsert_site
        manual_id = upsert_site(conn, "hand-added-affiliate.example", source="manual")
        conn.execute("UPDATE sites SET source='manual' WHERE id=?", (manual_id,))
        ownership.set_classification(conn, manual_id, "affiliate", admin="dan")
        ownership.upsert_contact(conn, manual_id, company_name="Hand Added Ltd",
                                 contact_email="hi@hand-added-affiliate.example",
                                 contact_telegram="@handadded",
                                 uploaded_by="inline_edit")

        # --- human actions (admin) -----------------------------------------
        ownership.set_classification(conn, site_id, "affiliate", admin="dan")
        ownership.upsert_contact(
            conn, site_id, company_name="CasinoPilot Media Ltd",
            contact_name="A. Manager", contact_email="deals@casinopilot-uk.example",
            contact_telegram="@casinopilot_deals", uploaded_by="csv_import_042")
        conn.commit()

        def snap():
            s = conn.execute("SELECT classification, source, etv, trend_pct, "
                             "top_country FROM sites WHERE id=?", (site_id,)).fetchone()
            c = conn.execute("SELECT company_name, contact_email, contact_telegram "
                             "FROM site_contacts WHERE site_id=?", (site_id,)).fetchone()
            n = conn.execute("SELECT COUNT(*) n FROM traffic_snapshots WHERE site_id=?",
                             (site_id,)).fetchone()["n"]
            return {
                "classification": s["classification"], "source": s["source"],
                "etv": s["etv"], "trend_pct": s["trend_pct"], "top_country": s["top_country"],
                "company_name": c["company_name"] if c else None,
                "contact_email": c["contact_email"] if c else None,
                "contact_telegram": c["contact_telegram"] if c else None,
                "snapshots": n,
            }

        before = snap()

        # --- API refresh with DIFFERENT traffic numbers --------------------
        refresh_all(conn, week_index=2)
        refresh_all(conn, week_index=3)
        after = snap()

        # --- report --------------------------------------------------------
        rows = [
            ("OWNER",  "FIELD",           "BEFORE",              "AFTER",               "RULE"),
        ]
        def fmt(v): return "—" if v is None else str(v)
        checks = [
            ("human", "classification", before["classification"], after["classification"], "unchanged"),
            ("human", "company_name",   before["company_name"],   after["company_name"],   "unchanged"),
            ("human", "contact_email",  before["contact_email"],  after["contact_email"],  "unchanged"),
            ("human", "contact_telegram", before["contact_telegram"], after["contact_telegram"], "unchanged"),
            ("api",   "etv",            before["etv"],            after["etv"],            "may change"),
            ("api",   "trend_pct",      before["trend_pct"],      after["trend_pct"],      "may change"),
            ("api",   "top_country",    before["top_country"],    after["top_country"],    "may change"),
        ]
        w = [5, 15, 22, 22, 10]
        def line(cols): print("  " + "  ".join(str(c).ljust(w[i]) for i, c in enumerate(cols)))
        line(rows[0])
        line(["-"*x for x in w])
        ok = True
        for owner, field, b, a, rule in checks:
            changed = fmt(b) != fmt(a)
            if owner == "human":
                verdict = "✅" if not changed else "❌ WIPED"
                ok = ok and not changed
            else:
                verdict = "✅ updated" if changed else "· same"
            line([owner, field, fmt(b), fmt(a), rule + " " + verdict])

        # --- hard guardrail: illegal write is refused, not silently ignored -
        _banner("GUARDRAIL — an API write to a human column is REFUSED")
        try:
            ownership.apply_api_update(conn, site_id,
                                       {"classification": "rejected"},
                                       source="dataforseo:labs")
            print("  ❌ guardrail FAILED: illegal write was allowed"); ok = False
        except ownership.OwnershipViolation as e:
            print(f"  ✅ OwnershipViolation raised: {e}")

        # --- manual-only site survived every refresh -----------------------
        _banner("MANUAL SITE — a hand-added site with no API presence survives")
        m = conn.execute("SELECT s.classification, s.etv, c.contact_email "
                         "FROM sites s LEFT JOIN site_contacts c ON c.site_id=s.id "
                         "WHERE s.id=?", (manual_id,)).fetchone()
        m_ok = (m is not None and m["classification"] == "affiliate"
                and m["contact_email"] == "hi@hand-added-affiliate.example")
        print(f"  hand-added-affiliate.example: classification={m['classification']}, "
              f"etv={m['etv']}, contact={m['contact_email']}  "
              + ("✅ intact" if m_ok else "❌ lost"))
        ok = ok and m_ok

        _banner("RESULT: " + ("✅ PASS — human data survived the refresh"
                              if ok else "❌ FAIL"))
        if not ok:
            sys.exit(1)
    finally:
        conn.close()


def cmd_admin(args) -> None:
    from .admin import serve
    serve(port=args.port)


def cmd_chat(args) -> None:
    from .chat import service
    from .chat.cometchat import CometChatClient
    conn = connect()
    client = CometChatClient()
    try:
        if args.chat_cmd == "provision":
            _banner("CHAT — provision rooms as CometChat groups")
            r = service.provision_rooms(conn, client)
            print(f"  {r['rooms']} rooms provisioned")
            for c in r["calls"]:
                print(f"    → {c}")
        elif args.chat_cmd == "seed":
            _banner("CHAT — seed demo managers + rooms + reports")
            print("  " + str(service.seed_demo(conn, client)))
        elif args.chat_cmd == "session":
            _banner(f"CHAT — issue session for '{args.manager}'")
            m = service.manager_by_handle(conn, args.manager)
            if not m:
                print("  no such manager"); return
            try:
                s = service.issue_session(conn, m["id"], client)
                print(f"  ✅ session issued ({s['_mode']}) for {m['handle']} ({s['uid']})")
                print(f"     authToken : {s['authToken']}")
                print(f"     groups    : {', '.join(s['allowedGroups'])}")
                print("     CometChat calls made:")
                for c in s["_calls"]:
                    print(f"       → {c}")
            except service.NotVerified as e:
                print(f"  🔒 403 NOT_VERIFIED ({e}) — app shows the 'verify to join' screen")
        elif args.chat_cmd == "reports":
            _banner("CHAT — open moderation queue")
            for r in service.list_reports(conn):
                print(f"  #{r['id']}  {r['room_guid']}  reported={r['reported_uid']}  "
                      f"reason={r['reason']}")
                print(f"       ↳ evidence: {r['snapshot']}")
        elif args.chat_cmd == "demo":
            _banner("CHAT — end-to-end demo")
            print(config.mode_banner())
            print("  " + str(service.seed_demo(conn, client)))
            for who in ("Sarah K", "Alex T"):
                m = service.manager_by_handle(conn, who)
                try:
                    s = service.issue_session(conn, m["id"], client)
                    print(f"  ✅ {who}: session OK ({s['_mode']}) uid={s['uid']} token={s['authToken'][:16]}…")
                except service.NotVerified as e:
                    print(f"  🔒 {who}: 403 NOT_VERIFIED ({e}) → locked 'verify to join' screen")
            print("  open reports:")
            for r in service.list_reports(conn):
                print(f"     #{r['id']} {r['room_guid']} reason={r['reason']} ↳ {r['snapshot']}")
            print("  → moderate report #1 as 'ban'")
            print("    " + str(service.moderate(conn, service.list_reports(conn)[-1]["id"], "ban", client=client)))
    finally:
        conn.close()


def cmd_swap(args) -> None:
    from . import swaps
    conn = connect()
    try:
        if args.swap_cmd == "seed":
            _banner("AFFSWAP — seed demo managers, affiliates, wants/haves + matches")
            print("  " + str(swaps.seed_demo(conn)))
        elif args.swap_cmd == "matches":
            _banner(f"AFFSWAP — matches for '{args.manager}'")
            m = _manager(conn, args.manager)
            if not m:
                print("  no such manager"); return
            rem = swaps.remaining(conn, m["id"])
            print(f"  swaps left: {'∞' if rem is None else rem}")
            for x in swaps.list_matches(conn, m["id"]):
                you = "✓ you agreed" if x["you_agreed"] else "you: not yet"
                they = "✓ agreed" if x["they_agreed"] else "waiting"
                mark = {"completed": "🔄 SWAPPED", "ready": "● ready", "void": "· void"}[x["status"]]
                print(f"  #{x['match_id']} {mark}  with {x['with']:<8}  "
                      f"give {x['you_give']:<28} get {x['you_get']}")
                print(f"       {you} · them: {they}"
                      + (f"  →  unlocked: {x['unlocked_contact']}" if x["unlocked_contact"] else ""))
        elif args.swap_cmd == "agree":
            m = _manager(conn, args.manager)
            if not m:
                print("  no such manager"); return
            try:
                r = swaps.agree(conn, args.match, m["id"])
            except swaps.SwapError as e:
                print(f"  ⚠ {e}"); return
            _banner(f"AFFSWAP — {args.manager} agrees to match #{args.match}")
            if r["status"] == "completed":
                print("  🔄 both agreed — contacts transferred:")
                for t in r.get("transfers", []):
                    print(f"     → {t['to']:<8} receives {t['site']}  ::  {t['contact']}")
            elif r["status"] == "needs_swaps":
                print(f"  ⏳ both agreed, but out of swaps: {', '.join(r['who'])} "
                      f"(top up at {r['topup']} or upgrade)")
            else:
                print(f"  ⏳ {args.manager} agreed — waiting on {r.get('waiting_on')}")
        elif args.swap_cmd == "ledger":
            _banner("AFFSWAP — swaps ledger (operator audit trail)")
            rows = swaps.ledger(conn, flagged_only=args.flagged)
            if not rows:
                print("  (no transfers yet)")
            for r in rows:
                flag = "  ⚑ FLAGGED" if r["flagged"] else ""
                print(f"  #{r['id']}  {r['from']:>8} → {r['to']:<8}  {r['domain']:<28} "
                      f"{r['contact']}{flag}")
                if r["flag_reason"]:
                    print(f"       ↳ {r['flag_reason']}")
        elif args.swap_cmd == "access":
            m = _manager(conn, args.manager)
            if not m:
                print("  no such manager"); return
            a = swaps.access(conn, m["id"])
            _banner(f"AFFSWAP — access for '{args.manager}'")
            print(f"  state      : {a['state'].upper()}   (can browse: {a['can_browse']})")
            print(f"  swaps      : {'∞' if a['unlimited'] else a['swaps']}")
            if a["trial_ends_at"]:
                print(f"  trial ends : {a['trial_ends_at']}"
                      + (f"  ({a['trial_hours_left']}h left)" if a["in_trial"] else "  (expired)"))
            print(f"  message    : {a['message']}")
        elif args.swap_cmd == "rewards":
            _banner("AFFSWAP — loyalty: new-affiliate submissions (Rewards queue)")
            for c in swaps.list_contributions(conn, "pending"):
                print(f"  #{c['id']}  {c['domain']:<24} by {c['by']:<8} "
                      f"{c['country'] or '—'}/{c['vertical'] or '—'}  reward +{c['reward']} swap")
                if c["comment"]:
                    print(f"       ↳ {c['comment']}")
            print("  (approve in the back office → grants the free swap + queues the site)")
        elif args.swap_cmd == "demo":
            _banner("AFFSWAP — end-to-end: match → both agree → transfer → ledger")
            print(config.mode_banner())
            print("  seed: " + str(swaps.seed_demo(conn)))
            sarah = _manager(conn, "Sarah K")
            print(f"\n  Sarah's matches (plan: Standard, {swaps.remaining(conn, sarah['id'])} swaps left):")
            for x in swaps.list_matches(conn, sarah["id"]):
                st = {"completed": "SWAPPED", "ready": "ready", "void": "void"}[x["status"]]
                print(f"    #{x['match_id']} [{st}] with {x['with']:<8} "
                      f"give {x['you_give']:<28} get {x['you_get']}"
                      + ("  (you agreed, waiting)" if x["you_agreed"] and x["status"] != "completed" else ""))
            # complete the Mike R match: Mike agrees, then Sarah agrees -> transfer
            mike = _manager(conn, "Mike R")
            mm = next(x for x in swaps.list_matches(conn, sarah["id"]) if x["with"] == "Mike R")
            print(f"\n  → Mike R agrees to #{mm['match_id']}…")
            swaps.agree(conn, mm["match_id"], mike["id"])
            print(f"  → Sarah K agrees to #{mm['match_id']}…")
            r = swaps.agree(conn, mm["match_id"], sarah["id"])
            for t in r.get("transfers", []):
                print(f"     🔄 {t['to']:<8} receives {t['site']}  ::  {t['contact']}")
            print(f"  Sarah now has {swaps.remaining(conn, sarah['id'])} swaps left (spent 1).")
            print("\n  Operator ledger (who sent what to whom):")
            for lr in swaps.ledger(conn):
                print(f"    {lr['from']:>8} → {lr['to']:<8} {lr['domain']:<28} {lr['contact']}")
            # operator flags a bad transfer + bans the sender
            first = swaps.ledger(conn)[-1]
            print(f"\n  → operator flags ledger #{first['id']} as 'wrong contact' and BANS {first['from']}")
            print("    " + str(swaps.flag_transfer(conn, first["id"], "wrong contact sent", ban=True)))

            # loyalty: submissions are candidates in the New-affiliate queue; the
            # free swap is granted ONLY when an admin approves the site there.
            print("\n  Loyalty — new-affiliate submissions awaiting admin review:")
            subs = swaps.list_contributions(conn, "pending")
            for c in subs:
                print(f"    #{c['id']} {c['domain']:<24} by {c['by']:<8} "
                      f"{c['country'] or '—'}/{c['vertical'] or '—'}  candidate · +{c['reward']} swap on approval")
            if subs:
                c0 = subs[0]
                mgr = _manager(conn, c0["by"])
                before = swaps.remaining(conn, mgr["id"])
                # admin approves the SITE in the queue → publishes it → grants the swap
                ownership.set_classification(conn, c0["site_id"], "affiliate", admin="operator")
                conn.commit()
                r = swaps.on_site_published(conn, c0["site_id"], by="operator")
                print(f"  → admin approves {c0['domain']} → published live + "
                      f"+{r['granted']} swap to {r['to']} ({before} → {r['swaps_left']})")
                print(f"  → a rejected submission would grant nothing.")

            # access gate: 48h trial from approval, then browsing needs >=1 swap
            print("\n  Access gate — 48h free trial, then browsing needs ≥1 swap:")
            for who in ("Tom B", "Priya N"):
                mm = _manager(conn, who); ac = swaps.access(conn, mm["id"])
                print(f"    {who:<8} [{ac['state'].upper():<7}] "
                      f"swaps={'∞' if ac['unlimited'] else ac['swaps']}  · {ac['message']}")
    finally:
        conn.close()


def _manager(conn, handle):
    return conn.execute("SELECT * FROM chat_managers WHERE handle=? OR cc_uid=?",
                        (handle, handle)).fetchone()


def cmd_alerts(args) -> None:
    from . import alerts
    conn = connect()
    _LBL = {"new": "✦ NEW", "up": "▲ UP", "down": "▼ DOWN"}
    try:
        if args.alerts_cmd == "seed":
            _banner("ALERTS — arm demo rules")
            print("  " + str(alerts.seed_demo(conn)))
        elif args.alerts_cmd == "list":
            m = _manager(conn, args.manager)
            _banner(f"ALERTS — rules for '{args.manager}'")
            for r in alerts.list_rules(conn, m["id"] if m else None):
                print(f"  #{r['id']} {'●' if r['active'] else '○ paused'} {r['summary']}")
        elif args.alerts_cmd == "add":
            m = _manager(conn, args.manager)
            if not m:
                print("  no such manager"); return
            try:
                r = alerts.save_rule(conn, m["id"], args.country,
                                     args.verticals.split(","), args.triggers.split(","),
                                     args.delivery.split(","))
                print(f"  armed #{r['id']}: {r['summary']}")
            except alerts.AlertError as e:
                print(f"  ⚠ {e}")
        elif args.alerts_cmd in ("evaluate", "fire"):
            m = _manager(conn, args.manager)
            rec = args.alerts_cmd == "fire"
            _banner(f"ALERTS — {'firing + delivering' if rec else 'evaluating (preview)'}"
                    + (f" · {args.manager}" if m else " · all members"))
            fired = alerts.evaluate(conn, m["id"] if m else None, record=rec)
            if not fired:
                print("  (nothing fires against current data)")
            for e in fired:
                via = ("→ delivered " + ",".join(e.get("delivered", []))) if rec \
                      else ("would notify " + ",".join(e["delivery"]))
                print(f"  {_LBL[e['event']]:<8} {e['name']:<22} {e['detail']:<14} {via}")
        elif args.alerts_cmd == "demo":
            _banner("ALERTS — arm rules → evaluate against live data → deliver")
            print("  " + str(alerts.seed_demo(conn)))
            fired = alerts.evaluate(conn, record=True)
            byrule, order = {}, []
            for e in fired:
                if e["rule_id"] not in byrule:
                    order.append(e["rule_id"]); byrule[e["rule_id"]] = []
                byrule[e["rule_id"]].append(e)
            rules = {r["id"]: r for r in alerts.list_rules(conn)}
            print(f"  {len(fired)} alert(s) delivered across {len(order)} rule(s):")
            for rid in order:
                evs = byrule[rid]
                print(f"\n    Rule #{rid} · {rules[rid]['summary']}  ({len(evs)} events)")
                for e in evs[:4]:
                    print(f"       {_LBL[e['event']]:<8} {e['name']:<22} {e['detail']:<14} "
                          f"→ {','.join(e.get('delivered', []))}")
                if len(evs) > 4:
                    print(f"       … +{len(evs)-4} more")
            print("\n  Re-running 'fire' won't re-notify (idempotent). Delivery is the plug-in "
                  "point — push/email/telegram wire in at alerts.deliver().")
    finally:
        conn.close()


def cmd_members(args) -> None:
    from . import members
    conn = connect()
    try:
        if args.members_cmd == "demo":
            _banner("SIGN-UP — apply → review → approve → 48h trial starts")
            print("  " + str(members.seed_demo(conn)))
            print("\n  Applications awaiting review:")
            for a in members.list_applications(conn, "pending"):
                sig = ("LinkedIn ✓" if a["linkedin_verified"] else "LinkedIn ✗") + \
                      (" · email ✓" if a["work_email_confirmed"] else " · email …")
                print(f"    #{a['id']} {(a['real_name'] or '—'):<13} {(a['company'] or '—'):<20} "
                      f"[{sig}]  {'READY' if a['ready_for_review'] else 'waiting'}")
            print("\n  Admin decisions:")
            for a in members.list_applications(conn, "pending"):
                if a["ready_for_review"]:
                    r = members.approve(conn, a["id"], by="operator")
                    print(f"    ✓ approved {a['real_name']} → {r.get('state')} · trial ends {r.get('trial_ends_at')}")
                else:
                    print(f"    … {a['real_name']} left pending (work email not confirmed)")
        elif args.members_cmd == "list":
            _banner("SIGN-UP — pending applications")
            for a in members.list_applications(conn, "pending"):
                print(f"  #{a['id']} {a['real_name']} · {a['company']} · "
                      f"{'ready' if a['ready_for_review'] else 'waiting on verification'}")
        elif args.members_cmd == "approve":
            print("  " + str(members.approve(conn, args.id, by="cli")))
        elif args.members_cmd == "reject":
            print("  " + str(members.reject(conn, args.id, by="cli")))
    finally:
        conn.close()


def cmd_dataforseo(args) -> None:
    """Connection + endpoint self-test. Runs the three calls the pipeline relies
    on (SERP, bulk traffic, per-country split). In MOCK it proves the code path;
    once DATAFORSEO_LOGIN/PASSWORD are in .env it hits the real API."""
    from .providers.dataforseo import DataForSEOClient, DataForSEOError
    from .locations import code_for
    live = config.dataforseo_is_live()
    _banner(f"DATAFORSEO — self-test ({'LIVE' if live else 'MOCK'})")
    if not live:
        print("  No live keys — running against fixtures. Add DATAFORSEO_LOGIN + "
              "DATAFORSEO_PASSWORD to .env and re-run to hit the real API.")
    client = DataForSEOClient()
    gb = code_for("GB")
    sample = ([args.domain] if args.domain else
              (["gambling.com", "livescore.com", "askgamblers.com"] if live
               else ["topcasinoreviews-uk.example", "casinopilot-uk.example"]))
    try:
        items = client.serp_organic("best online casino", gb, "en")
        print(f"  1) SERP @ GB → {len(items)} organic results; "
              f"top: {items[0]['domain'] if items else '—'}")
        etv = client.bulk_domain_traffic(sample, gb, "en")
        print("  2) Bulk traffic estimation @ GB:")
        for d in sample:
            print(f"       {d:<30} {int(etv.get(d, 0)):>10,}/mo")
        markets = [c for c in (code_for(x) for x in ("GB", "DE", "US", "IE")) if c]
        tr = client.domain_traffic(sample[0], markets)
        print(f"  3) {sample[0]} across GB/DE/US/IE → total {int(tr['total_etv']):,}/mo · "
              f"{tr['by_country']}")
        print(f"  ✅ Pipeline OK ({'LIVE — real DataForSEO data' if live else 'MOCK — fixtures'}). "
              "A full refresh = one bulk call per market.")
    except DataForSEOError as e:
        print(f"  ✗ {e}")
        print("    401 = check login/password · 402 = no funds/quota · 403 = endpoint not enabled.")


def cmd_import(args) -> None:
    """Bulk-import affiliate sites from a CSV file — for loading a known list at
    scale (e.g. your ~1000 vetted sites) from the command line."""
    from . import importer
    try:
        text = open(args.file, encoding="utf-8").read()
    except OSError as e:
        print(f"  cannot read {args.file}: {e}"); return
    rows, errs = importer.parse_csv(text)
    if errs:
        print("  ⚠ " + errs[0]); return
    conn = connect()
    try:
        _banner(f"BULK IMPORT — {args.file}")
        summary = importer.apply(conn, rows, uploaded_by="cli_import")
        print(f"  {summary['created']} created · {summary['updated']} updated · "
              f"{summary['skipped']} skipped · {summary['total']} rows")
        print(config.mode_banner().split('|')[0].strip())
        print("  Next → `radar refresh` (with DataForSEO live) pulls traffic + geos for every")
        print("         imported site; `radar sheets sync` puts them all in your Google Sheet to review.")
    finally:
        conn.close()


def cmd_sheets(args) -> None:
    from . import sheets
    conn = connect()
    try:
        if args.sheets_cmd == "rows":
            _banner("GOOGLE SHEETS — row preview")
            aff = sheets.affiliate_rows(conn, args.affiliates_only)
            sw = sheets.swap_rows(conn)
            print(f"  Affiliates: {len(aff)} rows · cols: {', '.join(sheets.AFFILIATE_HEADER)}")
            for row in aff[:3]:
                print("    " + " | ".join(str(x) for x in row[:8]))
            print(f"  Swaps: {len(sw)} rows · cols: {', '.join(sheets.SWAP_HEADER)}")
            for row in sw[:3]:
                print("    " + " | ".join(str(x) for x in row))
        else:  # sync / demo
            _banner("GOOGLE SHEETS — sync affiliate DB + swaps log")
            print("  " + config.mode_banner().split("|")[-1].strip())
            r = sheets.sync_all(conn, only_affiliates=args.affiliates_only)
            for tab in ("affiliates", "swaps"):
                x = r[tab]
                loc = f"  → {x['file']}" if x.get("file") else ""
                print(f"  {tab.title():<11} [{x['mode']}]  {x.get('rows', '?')} rows{loc}")
            if r["affiliates"]["mode"] == "MOCK":
                print("  MOCK — wrote local CSVs. Add SHEETS_WEBAPP_URL + SHEETS_SECRET to .env "
                      "(see docs/google-sheets-integration.md) to push to a live Google Sheet.")
    finally:
        conn.close()


def cmd_demo(args) -> None:
    print(config.mode_banner())
    init_db(reset=True)
    conn = connect()
    try:
        _banner("DISCOVERY — worldwide sweep (casino) + curated GB verticals")
        pairs = keywords.world_markets("casino") + [
            ("GB", "sportsbook"), ("GB", "bingo"), ("GB", "poker")]
        total_new = 0
        countries_hit = set()
        for country, vertical in pairs:
            r = discover(conn, country, vertical)
            total_new += r["new_candidates"]
            if r["hits"]:
                countries_hit.add(r["country"])
        print(f"  swept {len(keywords.world_markets('casino'))} geos · "
              f"{len(countries_hit)} markets returned results · "
              f"{total_new} new candidate domains")
    finally:
        conn.close()

    class A: pass
    a = A(); a.build_history = True; a.week = LATEST_WEEK
    cmd_refresh(a)
    s = A(); s.all = True; s.domain = None
    cmd_screenshot(s)

    # --- review queue: a human approves the real affiliates ----------------
    # Discovery surfaced 6 domains; an operator confirms 4 as genuine
    # affiliates (published to the app) and leaves 2 in the holding queue.
    _banner("REVIEW QUEUE — human approves the genuine affiliates")
    conn = connect()
    try:
        from datetime import datetime, timedelta, timezone
        approvals = {
            "topcasinoreviews-uk.example": {"days_ago": 2,  "email": "partners@topcasinoreviews-uk.example", "teams": "partners@topcasinoreviews-uk.example"},
            "casinopilot-uk.example":      {"days_ago": 40, "email": "deals@casinopilot-uk.example", "telegram": "@casinopilot_deals"},
            "slotwise-uk.example":         {"days_ago": 3,  "telegram": "@slotwise"},
            "reelmentor.example":          {"days_ago": 50},
        }
        queued = ["thegamblingcompass.example", "bonusradar-uk.example"]
        for domain, meta in approvals.items():
            row = conn.execute("SELECT id FROM sites WHERE domain=?", (domain,)).fetchone()
            if not row:
                continue
            sid = row["id"]
            ownership.set_classification(conn, sid, "affiliate", admin="reviewer")
            # backdate published_at so some read as NEW and some don't
            pub = (datetime.now(timezone.utc) - timedelta(days=meta["days_ago"])).replace(microsecond=0).isoformat()
            conn.execute("UPDATE sites SET published_at=? WHERE id=?", (pub, sid))
            if any(k in meta for k in ("email", "telegram", "teams")):
                ownership.upsert_contact(conn, sid,
                    contact_email=meta.get("email"), contact_telegram=meta.get("telegram"),
                    contact_teams=meta.get("teams"), uploaded_by="reviewer")
            print(f"  ✓ approved {domain}  (published {meta['days_ago']}d ago)")
        for domain in queued:
            print(f"  · left in queue: {domain}")

        # Worldwide: simulate reviewers approving the top candidate in a spread
        # of other markets, so the worldwide home + country browse are populated.
        world_countries = ["US", "DE", "CA", "AU", "BR", "SE", "IE", "ES",
                           "IT", "NZ", "JP", "ZA", "FR", "PL"]
        approved_world = 0
        deep = {"US", "DE", "BR", "AU"}  # deeper markets for the Movers view
        for i, iso in enumerate(world_countries):
            limit = 6 if iso in deep else 2
            cands = conn.execute(
                """SELECT DISTINCT s.id FROM sites s JOIN discovery_hits h ON h.site_id=s.id
                   WHERE h.country=? AND s.classification='candidate'
                   ORDER BY (s.etv IS NULL), s.etv DESC LIMIT ?""", (iso, limit)).fetchall()
            for r in cands:
                ownership.set_classification(conn, r["id"], "affiliate", admin="reviewer")
                pub = (datetime.now(timezone.utc) - timedelta(days=(i % 20))).replace(microsecond=0).isoformat()
                conn.execute("UPDATE sites SET published_at=? WHERE id=?", (pub, r["id"]))
                approved_world += 1
        print(f"  ✓ approved {approved_world} affiliates across {len(world_countries)} more markets")
        conn.commit()

        # seed illustrative peer-review aggregates (Phase 2) for the sort-by-reviews view
        import hashlib
        seeded = 0
        for r in conn.execute("SELECT id, domain FROM sites WHERE classification='affiliate'").fetchall():
            h = int(hashlib.md5(r["domain"].encode()).hexdigest()[:8], 16)
            ownership.set_review_summary(conn, r["id"],
                                         round(3.3 + (h % 17) / 10.0, 1), 6 + (h % 40),
                                         admin="seed")
            seeded += 1
        conn.commit()
        print(f"  ★ seeded peer-review scores for {seeded} affiliates (Phase 2 illustrative)")

        # blacklist upload (manual CSV) — flagged bad actors shown with a reason + market
        from . import importer
        bl_rows, _ = importer.parse_blacklist_csv(importer.BLACKLIST_TEMPLATE)
        bl = importer.apply_blacklist(conn, bl_rows, added_by="reviewer")
        print(f"  ⚠ blacklisted {bl['created']} sites from manual upload")
    finally:
        conn.close()

    def A_(**kw):
        o = A()
        for k in ("what", "json", "country", "vertical", "domain", "contact", "sort"):
            setattr(o, k, kw.get(k))
        o.json = kw.get("json", False)
        return o

    cmd_show(A_(what="home"))                                            # flag grid
    cmd_show(A_(what="movers", country="GB"))
    # country list under the Casino tab, shown in all three sort modes
    cmd_show(A_(what="country", country="GB", vertical="casino", sort="traffic"))
    cmd_show(A_(what="country", country="GB", vertical="casino", sort="reviews"))
    cmd_show(A_(what="country", country="GB", sort="blacklisted"))
    cmd_show(A_(what="profile", domain="casinopilot-uk.example", contact=True))
    cmd_prove_ownership(A())

    # prove-ownership replays older weeks; restore every site to the latest week
    conn = connect()
    try:
        refresh_all(conn, week_index=LATEST_WEEK)
    finally:
        conn.close()


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="radar", description="AffiliateRadar data-layer PoC")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("initdb"); q.add_argument("--reset", action="store_true")
    q.set_defaults(func=cmd_initdb)

    q = sub.add_parser("discover")
    q.add_argument("--country", default="GB"); q.add_argument("--vertical", default="casino")
    q.add_argument("--all", action="store_true", help="all curated markets")
    q.add_argument("--world", action="store_true", help="sweep every known geo (one vertical)")
    q.set_defaults(func=cmd_discover)

    q = sub.add_parser("refresh")
    q.add_argument("--week", type=int, default=LATEST_WEEK)
    q.add_argument("--build-history", action="store_true",
                   help="run weeks 0..N to seed trend history")
    q.set_defaults(func=cmd_refresh)

    q = sub.add_parser("history", help="build real N-month monthly traffic history")
    q.add_argument("--months", type=int, default=12)
    q.set_defaults(func=cmd_history)

    q = sub.add_parser("screenshot")
    q.add_argument("--domain"); q.add_argument("--all", action="store_true")
    q.add_argument("--limit", type=int, help="cap how many sites to capture (highest traffic first)")
    q.add_argument("--missing", action="store_true", help="only sites without a screenshot yet")
    q.set_defaults(func=cmd_screenshot)

    q = sub.add_parser("show")
    q.add_argument("what", choices=["home", "countries", "movers", "blacklist", "country", "profile"])
    q.add_argument("--country", default="GB"); q.add_argument("--vertical")
    q.add_argument("--sort", choices=["traffic", "reviews", "blacklisted"], default="traffic")
    q.add_argument("--domain"); q.add_argument("--contact", action="store_true")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_show)

    q = sub.add_parser("prove-ownership"); q.set_defaults(func=cmd_prove_ownership)
    q = sub.add_parser("demo"); q.set_defaults(func=cmd_demo)

    q = sub.add_parser("admin", help="launch the back-office web panel")
    q.add_argument("--port", type=int, default=8765)
    q.set_defaults(func=cmd_admin)

    q = sub.add_parser("chat", help="community chat (CometChat) backend")
    q.add_argument("chat_cmd", choices=["provision", "seed", "session", "reports", "demo"])
    q.add_argument("--manager", default="Sarah K", help="handle or cc_uid (for 'session')")
    q.set_defaults(func=cmd_chat)

    q = sub.add_parser("swap", help="Affswap peer-to-peer swap engine")
    q.add_argument("swap_cmd", choices=["seed", "matches", "agree", "ledger", "rewards", "access", "demo"])
    q.add_argument("--manager", default="Sarah K", help="handle or cc_uid")
    q.add_argument("--match", type=int, help="match id (for 'agree')")
    q.add_argument("--flagged", action="store_true", help="ledger: only flagged transfers")
    q.set_defaults(func=cmd_swap)

    q = sub.add_parser("alerts", help="alerts engine (rules + matcher + delivery hook)")
    q.add_argument("alerts_cmd", choices=["seed", "list", "add", "evaluate", "fire", "demo"])
    q.add_argument("--manager", default="Sarah K", help="handle or cc_uid")
    q.add_argument("--country", default="", help="ISO/name, or blank for all markets (add)")
    q.add_argument("--verticals", default="casino", help="CSV: casino,sportsbook,bingo,poker")
    q.add_argument("--triggers", default="new,up,down", help="CSV subset of new,up,down")
    q.add_argument("--delivery", default="push", help="CSV subset of push,email,telegram")
    q.set_defaults(func=cmd_alerts)

    q = sub.add_parser("members", help="sign-up applications + member approvals")
    q.add_argument("members_cmd", choices=["demo", "list", "approve", "reject"])
    q.add_argument("--id", type=int, help="application id (approve/reject)")
    q.set_defaults(func=cmd_members)

    q = sub.add_parser("dataforseo", help="test the live DataForSEO connection")
    q.add_argument("dfs_cmd", choices=["test"], nargs="?", default="test")
    q.add_argument("--domain", help="a domain to probe (default: a few known ones)")
    q.set_defaults(func=cmd_dataforseo)

    q = sub.add_parser("import", help="bulk-import affiliate sites from a CSV file")
    q.add_argument("file", help="path to CSV: url,country,vertical,company,website_name,"
                                "email,telegram,teams,status (only url required)")
    q.set_defaults(func=cmd_import)

    q = sub.add_parser("sheets", help="Google Sheets mirror (affiliate DB + swaps log)")
    q.add_argument("sheets_cmd", choices=["sync", "rows", "demo"])
    q.add_argument("--affiliates-only", action="store_true",
                   help="only published affiliates (skip candidates)")
    q.set_defaults(func=cmd_sheets)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
