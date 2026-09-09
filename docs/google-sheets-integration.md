# Google Sheets mirror — setup

Affswap mirrors two things to a Google Sheet:

- **Affiliates** — one row per site: domain, name, verticals, status, total etv,
  primary geo, and the **full per-geo traffic split** (every market it draws
  traffic from), rebuilt on each sync.
- **Swaps** — appended the moment a contact changes hands: who sent which website
  + contact to whom, when.

There are **no Google client libraries** to install. Affswap POSTs JSON to a tiny
Apps Script **Web App** that's bound to your sheet. Until you set it up, everything
runs in MOCK mode and writes the exact same rows to local CSVs under
`data/sheets/` — so you can see (and import) precisely what will sync.

```bash
python3 -m radar sheets sync     # push catalogue + swaps (MOCK → data/sheets/*.csv)
python3 -m radar sheets rows     # preview the rows + columns without writing
```

Swaps also update the sheet **live** as they happen (the transfer step appends a
row); `sync` is for the full catalogue + a full backfill.

## Go live in 4 steps

1. **Create the sheet.** New Google Sheet → note nothing else; the tabs are
   created automatically.
2. **Add the Web App.** In the sheet: `Extensions → Apps Script`, replace the
   contents with the script below, and set `SECRET` to a long random string.
3. **Deploy.** `Deploy → New deployment → Web app`. Execute as **Me**; who has
   access **Anyone**. Copy the Web App URL.
4. **Point Affswap at it.** In `.env`:

   ```
   SHEETS_WEBAPP_URL=https://script.google.com/macros/s/AKfy…/exec
   SHEETS_SECRET=the-same-long-random-string
   ```

   That's it — `radar sheets sync` and every swap now write to the real sheet.
   The secret is server-only; the sheet stays private to your Google account.

## The Apps Script (paste into the bound project)

```javascript
const SECRET = 'the-same-long-random-string';   // must match SHEETS_SECRET in .env

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    if (body.secret !== SECRET) return _json({ ok: false, error: 'bad secret' });

    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sh = ss.getSheetByName(body.tab) || ss.insertSheet(body.tab);
    const rows = body.rows || [];

    if (body.action === 'replace') {
      sh.clearContents();
      sh.getRange(1, 1, 1, body.header.length).setValues([body.header]);
      if (rows.length) sh.getRange(2, 1, rows.length, body.header.length).setValues(rows);
    } else { // append
      if (sh.getLastRow() === 0) sh.appendRow(body.header);
      rows.forEach(r => sh.appendRow(r));
    }
    return _json({ ok: true, tab: body.tab, action: body.action, written: rows.length });
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}

function _json(o) {
  return ContentService.createTextOutput(JSON.stringify(o))
    .setMimeType(ContentService.MimeType.JSON);
}
```

## Notes

- **The sheet is a mirror, not the source of truth.** The catalogue lives in the
  database; the sheet is for browsing, sharing and analysis. Re-running `sync`
  rebuilds the Affiliates tab from the DB, so hand-edits there are overwritten —
  edit sites in the back office instead.
- **Scale.** Google Sheets tops out around 10M cells. For a very large catalogue,
  point the same rows at BigQuery or a database export instead — the row-builder
  (`radar/sheets.py`) is the only thing that changes.
- **Contact data.** Swap rows contain a person's business contact. Treat the sheet
  as confidential (it inherits your Google account's sharing — keep it private),
  and mind the GDPR posture in the project plan before sharing it around.
