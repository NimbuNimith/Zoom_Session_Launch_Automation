# Sessions CSV format

One CSV, both session types — a `Type` column tells the loader which row
is which. Column names below are exact matches from `_on_upload_csv()` in
`main_window.py`; where two names are listed, either is accepted.

## Columns

| Column | Required? | Applies to | Notes |
|---|---|---|---|
| `Type` | **Required** | Both | `ZOOM` or `PRISM` (case-insensitive). Anything else, the row is skipped. |
| `Meeting Link` (or `Meeting link`) | **Required** | Both | Row is skipped if blank. |
| `Program Name` | Optional | Both | |
| `Session Topic / Name` (or `Session Topic`) | Optional | Both | Shown in the grid's "Topic / Name" column. |
| `Moderator Name` | Optional | Both | Also used as the display name Playwright types in on join. |
| `Session Start Time` | Optional | Both | Feeds Auto Pilot's launch-window check. Accepts `HH:MM AM/PM`, `HH:MM` 24h, or ISO (`2026-07-06T19:05:00`, with or without a `Z`/UTC offset). |
| `Session End Time` (or `Ends At`) | Optional | Both | Stored, not currently used for any logic. |
| `Meeting Code` | Optional | Both | Display only. |
| `Host Email` | Zoom only | Zoom | Needed to launch as host (auto-login). Leave blank for guest-join rows. |
| `Host Password` | Zoom only | Zoom | |
| `Prism Type` (or `Session Type`) | **Required for the status-push** | Prism | Must be exactly `session`, `workshop-session`, or `session-group-session`. Anything else: the row still loads and launches fine, it just logs a warning and skips the Prism status-push. |
| `Prism Session ID` (or `Session ID`) | **Required for the status-push** | Prism | The Prism entity ID for all three types. |
| `Prism Session Group ID` (or `Session Group ID`) | **Required only if Prism Type = `session-group-session`** | Prism | Prism's API doesn't return this one — you have to supply it (see `prism_data_pull.py`'s docstring for why). |

## What happens with incomplete Prism metadata

Nothing blocks the CSV load. A row with a missing/invalid `Prism Type`, no
`Prism Session ID`, or (for session-group-session) no `Prism Session Group
ID` still loads and launches normally — you just won't get the automatic
Prism status-push for that row, and you'll see it flagged in the Event Log
plus in the load summary ("N Prism row(s) with incomplete metadata").

## Example

```csv
Type,Program Name,Session Topic,Moderator Name,Session Start Time,Meeting Link,Host Email,Host Password,Prism Type,Session ID,Session Group ID,Meeting Code
ZOOM,DBA Gen AI - Dissertation,Business Fundamentals,Nimith,19:05:00,https://upgrad.zoom.us/j/1111111,zoom126@upgrad.com,pw123,,,,
ZOOM,DBA Gen AI - Dissertation,Book Writing,Nimith,19:05:00,https://upgrad.zoom.us/j/2222222,zoom129@upgrad.com,pw123,,,,
PRISM,GGU DBA,Leadership Excellence,Nimith,19:05:00,https://upgrad.zoom.us/s/3333333?zak=ZZZ,,,session,6a3bc63d7bea4a16ab57a7fd,,101
PRISM,GGU DBA,Foundations of ML,Nimith,19:05:00,https://upgrad.zoom.us/s/4444444?zak=ZZZ,,,session-group-session,6a3bc710da696b8dbd65361c,6a3915fbc6e8e9b07bf5f8d8,102
PRISM,GGU DBA,AI Workshop,Nimith,19:05:00,https://upgrad.zoom.us/s/5555555?zak=ZZZ,,,workshop-session,6a3272b6a3f4819faba297fb,,103
```

Row-by-row: two plain Zoom sessions (one host launch, credentials
supplied); then all three Prism types — `session`, `session-group-session`
(note the extra Session Group ID), and `workshop-session` — showing the
full shape the loader expects.

## Getting the Prism columns filled in

You don't have to hand-fill `Prism Session ID` / Name / Status / Join URL
/ Meeting Code yourself — that's what `prism_data_pull.py` is for. Put
just the `Session ID` (and `Session Group ID`, for that one type) in an
Excel sheet, run it, and it pulls the rest from Prism directly into the
same file. See that script's docstring for usage.

---

## Google Sheets Apps Script Mapping

When using the **Sync from Google Sheets** feature, the Apps Script reads from three tabs
in the master spreadsheet and maps columns to the CSV format below.

### Source Tabs

| Tab | Purpose | Key Columns |
|-----|---------|-------------|
| `Combined` | Main session data | `Moderator`, `Session Platform`, `Session_Link`, `Topic`, `Program`, `Start Time`, `End Time`, `Date`, `Month`, `Year`, `Meeting IDs`, `Session Type`, `Session Code`, `ZoomAccount` |
| `Zoom Cred` | Zoom host credentials | `ZoomAccount`, `Zoom id`, `Pass` |
| `PRISM Group` | Prism Session Group IDs | `Program Name`, `Cohort ID`, `Group Session ID` |

### Column Mapping (Apps Script → CSV)

| CSV Column | Source | Transform |
|------------|--------|-----------|
| `Type` | `Combined!Session Platform` | "Zoom"→"ZOOM", "Prism"→"PRISM" |
| `Meeting Link` | **Constructed** | Zoom: `https://upgrad.zoom.us/j/{Meeting IDs no spaces}`<br>PRISM: Empty (fetched later via Prism API) |
| `Program Name` | `Combined!Program` | Direct |
| `Session Topic / Name` | `Combined!Topic` | Direct |
| `Moderator Name` | `Combined!Moderator` | Direct |
| `Session Start Time` | `Combined!Start Time` + `Date/Month/Year` | Combined → ISO `YYYY-MM-DDTHH:MM:SS` |
| `Session End Time` | `Combined!End Time` + `Date/Month/Year` | Combined → ISO |
| `Meeting Code` | `Combined!Meeting IDs` | Direct (with spaces for display) |
| `Host Email` | `Zoom Cred!Zoom id` | Joined via `ZoomAccount` |
| `Host Password` | `Zoom Cred!Pass` | Joined via `ZoomAccount` |
| `Prism Type` | `Combined!Session Type` | "Live Session Head"→"session", "Group session"→"session-group-session", "Normal Session"→"workshop-session" |
| `Prism Session ID` | `Combined!Meeting IDs` | Spaces removed (for API) |
| `Prism Session Group ID` | `PRISM Group!Group Session ID` | Joined via `Session Code`/`Cohort ID` (skipped for now) |

### Filtering Logic

- **Moderator**: Exact case-sensitive match on `Combined!Moderator`
- **Date**: `Combined!Date` + `Month` + `Year` parsed as YYYY-MM-DD, matched to target date
- **Platform**: Only rows where `Session Platform` = "Zoom" or "Prism"
- **Empty links**: Rows with empty `Session_Link` are skipped

### Missing Data Handling

| Missing Field | Behavior |
|---------------|----------|
| Zoom credentials not found | `Host Email`/`Host Password` left blank, warning logged |
| Prism Group ID not found | `Prism Session Group ID` left blank (editable in UI later) |
| Unknown Session Type | Defaults to "session", warning logged |
| Empty Meeting IDs | Row skipped entirely (required for both Zoom URL and Prism ID) |
| Date parse failure | Row skipped, warning logged |

---

## Google Sheets Apps Script Mapping

When using the **Sync from Google Sheets** feature, the Apps Script reads
from three tabs in the master spreadsheet and maps columns to the CSV
format below.

### Source Tabs

| Tab | Purpose | Key Columns |
|-----|---------|-------------|
| `Combined` | Main session data | `Moderator`, `Session Platform`, `Topic`, `Program`, `Start Time`, `End Time`, `Date`, `Month`, `Year`, `Meeting IDs`, `Session Type`, `Session Code`, `ZoomAccount` |
| `Zoom Cred` | Zoom host credentials | `ZoomAccount`, `Zoom id`, `Pass` |
| `PRISM Group` | Prism Session Group IDs | `Program Name`, `Cohort ID`, `Group Session ID` |

### Column Mapping (Apps Script → CSV)

| CSV Column | Source | Transform |
|------------|--------|-----------|
| `Type` | `Combined!Session Platform` | "Zoom"→"ZOOM", "Prism"→"PRISM", others filtered out |
| `Meeting Link` | **Constructed** | Zoom: `https://upgrad.zoom.us/j/{Meeting IDs no spaces}`<br>PRISM: Empty (fetched later via Prism API) |
| `Program Name` | `Combined!Program` | Direct |
| `Session Topic / Name` | `Combined!Topic` | Direct |
| `Moderator Name` | `Combined!Moderator` | Direct (filter key) |
| `Session Start Time` | `Combined!Start Time` + `Date/Month/Year` | Combined → ISO `YYYY-MM-DDTHH:MM:SS` |
| `Session End Time` | `Combined!End Time` + `Date/Month/Year` | Combined → ISO `YYYY-MM-DDTHH:MM:SS` |
| `Meeting Code` | `Combined!Meeting IDs` | Direct (with spaces for display) |
| `Host Email` | `Zoom Cred!Zoom id` | Joined via `ZoomAccount` |
| `Host Password` | `Zoom Cred!Pass` | Joined via `ZoomAccount` |
| `Prism Type` | `Combined!Session Type` | "Live Session Head"→"session", "Group session"→"session-group-session", "Normal Session"→"workshop-session" |
| `Prism Session ID` | `Combined!Meeting IDs` | Direct (spaces removed for API) |
| `Prism Session Group ID` | `PRISM Group!Group Session ID` | Joined via `Cohort ID` (skipped for now) |

### Filtering Logic

- **Moderator**: Exact case-sensitive match on `Combined!Moderator`
- **Date**: Parse `Date` + `Month` + `Year` → `YYYY-MM-DD`, match target date (default: today)
- **Platform**: Only rows where `Session Platform` = "Zoom" or "Prism"
- **Empty links**: Rows with empty `Meeting IDs` are skipped entirely

### Missing Data Handling

| Missing Field | Behavior |
|---------------|----------|
| Zoom credentials not found | `Host Email`/`Host Password` left blank, warning logged |
| Prism Group ID not found | `Prism Session Group ID` left blank (editable in preview dialog) |
| Unknown Session Type | Defaults to "session", warning logged |
| Empty Meeting IDs | Row skipped entirely |
| Date parse failure | Row skipped, warning logged |

### Zoom URL Construction

The Apps Script constructs Zoom join URLs from the `Meeting IDs` column:
```
Input:  "929 4483 1526" (with spaces)
Output: "https://upgrad.zoom.us/j/92944831526"
```
All whitespace is removed before appending to the base URL.

### PRISM Link Fetching

For PRISM sessions, the `Meeting Link` column is left **empty** in the Apps Script output. The `Prism Session ID` (from `Meeting IDs` with spaces removed) is used later in Command Center to fetch the actual join URL via the Prism API (`prism_data_pull.py` logic), triggered automatically by Auto Pilot or manually via the "Launch" button.
