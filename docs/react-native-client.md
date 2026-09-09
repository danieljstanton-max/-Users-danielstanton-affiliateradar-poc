# AffiliateRadar — React Native chat client (drop-in)

This is the **only** chat code that lives in the mobile app. It never touches the
CometChat REST key and never self-authenticates — it asks **your** backend for a
token, then logs in. Backend endpoints (already built in this repo):

- `POST /api/chat/session` → `{ appId, region, uid, authToken, allowedGroups }` or `403 { error: "NOT_VERIFIED" }`
- `POST /api/chat/report`  → `{ ok: true, report_id }`

> Install the CometChat SDK/UI Kit for React Native per their docs and **confirm
> current method names** — the calls below follow the reviewed spec and are
> marked where they must be validated.

```ts
import { CometChat } from "@cometchat/chat-sdk-react-native"; // CONFIRM package name/version

// 1) init once at app start (App ID + Region only — NO auth key in the bundle)
await CometChat.init(
  session.appId,
  new CometChat.AppSettingsBuilder().setRegion(session.region).subscribePresenceForAllUsers().build()
); // CONFIRM builder methods for your SDK major (UIKit v5 exists)

// 2) open a gated session — YOUR backend runs the verification gate
async function openChat(myAppJwt: string) {
  const res = await fetch("https://api.affiliateradar.app/api/chat/session", {
    method: "POST",
    headers: { Authorization: `Bearer ${myAppJwt}`, "Content-Type": "application/json" },
  });
  if (res.status === 403) return showVerifyToJoinScreen();   // not a verified manager
  const session = await res.json();

  // 3) log in with the token the backend minted (token path ONLY)
  await CometChat.login(session.authToken);                  // never login(uid, authKey)

  // store the token in secure storage (Keychain/Keystore), never AsyncStorage/logs
  for (const guid of session.allowedGroups) {
    try { await CometChat.joinGroup(guid /*, type, password */); } // CONFIRM signature
    catch (e: any) { if (e.code !== "ALREADY_JOINED") throw e; }    // CONFIRM code
  }
  // then mount CometChat's prebuilt UI (Chat Builder), themed to the neon palette:
  //   primaryColor = #38C6FF, dark ground — see cometchat-builder-settings.json
}

// 4) report a message → goes to YOUR backend (which fetches real evidence + queues it)
async function reportMessage(m, reason: string, myAppJwt: string) {
  await fetch("https://api.affiliateradar.app/api/chat/report", {
    method: "POST",
    headers: { Authorization: `Bearer ${myAppJwt}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      room_guid: m.getReceiverId(), message_id: m.getId(),
      reported_uid: m.getSender().getUid(), reporter_uid: CometChat.getLoggedinUser()?.getUid(),
      reason,
    }),
  });
}

// 5) block (personal) is client-side:
await CometChat.blockUsers([uid]); // CONFIRM method name
```

## The "plug in your Auth code" step
1. Put your CometChat **App ID + Region** in the app config, and the **REST API Key**
   in the *backend* `.env` (`COMETCHAT_REST_API_KEY`). The moment those are set,
   `radar.chat` flips from MOCK to LIVE — **no code change**.
2. Point `openChat()` / `reportMessage()` at your deployed backend URL.
3. Theme CometChat's Chat Builder to the neon palette (`#38C6FF`, dark).
4. Ship report + block + operator-remove (already wired) to satisfy Apple 1.2.

Full server-side details, security invariants, and the "confirm in docs" checklist:
see [`cometchat-integration.md`](./cometchat-integration.md).
