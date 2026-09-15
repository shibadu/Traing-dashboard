# Training performance dashboard

Pre-test vs post-test dashboard for a Google Forms training assessment.
"Form Responses 1" holds the pre-test, "Form Responses 2" holds the post-test.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Running automatically, with nothing to download

The sheet is shared as "Anyone with the link · Viewer", so **Published CSV link** (the
default in the sidebar) needs no setup at all — the app reads the live sheet on every
load. Data is cached 5 minutes; use the sidebar's "Refresh now" button to force an
immediate re-pull.

You only need one thing, already pre-filled in the sidebar:
- **Sheet ID** and both tab **gids** (`295127114` for the pre-test, `1831648219` for the
  post-test) — nothing to paste in, just run it.

**If you ever make the sheet private again**, switch to **Google service account**
instead — same live behaviour, works without public link-sharing:

- In Google Cloud Console: create a service account, enable the Google Sheets API, download the JSON key.
- Open the spreadsheet → Share → add the service account's `client_email` as a **Viewer**.
- Save the key as `.streamlit/secrets.toml` next to `app.py` (locally), or paste it into
  **Settings → Secrets** if this is deployed on Streamlit Community Cloud:

```toml
[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "...@....iam.gserviceaccount.com"
client_id = "..."
token_uri = "https://oauth2.googleapis.com/token"
```

**Fallbacks**, if you don't want to set up a service account:

- **Published CSV link** — set sheet sharing to "Anyone with the link · Viewer", then paste
  the `gid` of each tab (the number after `#gid=` when that tab is open).
- **Upload** — File > Download > CSV on each tab, then upload both. Manual, not automatic.

## Mapping the columns

The sidebar auto-detects the identifier column ("Enter your Unique ID..."), the score
column (it prefers **Corrected score** over **Score** when both exist — Google Forms'
auto-grade often reads 0 while the corrected column holds the real mark), and the class
column, so nothing needs setting for this form as it's currently built. Open **Column
mapping** in the sidebar only if the form itself changes.

Participants are matched on a normalised ID: digits are extracted from codes like
`TB-011`, `011`, `*110#` so they all collapse to the same key regardless of how someone
typed it. Free-text identifiers (names, emails) instead get word-order- and
punctuation-normalised. An optional close-match fallback catches remaining typos.

Any pre-test column (Class, county, facility, cadre) can be added as a breakdown field for
filters and a gain-by-group comparison — Class is included by default.
