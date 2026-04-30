# 🚀 Zoom Command Center

A desktop automation tool for managing multiple Zoom sessions simultaneously — built for upGrad's session operations team.

---

## ✨ Features

- Launch and monitor multiple Zoom sessions from a single dashboard
- Auto-Pilot mode — automatically launches sessions based on scheduled start times
- Host login with credential support per session
- Live heartbeat monitoring (detects LIVE / Waiting / Ended states)
- Manual retry on failed sessions
- Account slot limiting to avoid Zoom license conflicts
- CSV bulk upload for session schedules

---

## 🖥️ Requirements

- Windows 10 / 11
- Python 3.9 or higher → [Download here](https://python.org/downloads)

---

## ⚙️ First-Time Setup (Do this once)

**Step 1 — Download the project**

Click the green **Code** button above → **Download ZIP** → Extract the folder

Or if you have Git installed:
```bash
git clone https://github.com/YOUR_USERNAME/zoom-command-center.git
cd zoom-command-center
```

**Step 2 — Run the installer**

Double-click `install.bat`

This will automatically install all required packages and the Chromium browser.

---

## ▶️ Running the App

Double-click `run_app.bat` every time you want to launch the app.

---

## 📋 CSV Format

Your session schedule CSV must have these columns:

| Column | Description | Example |
|---|---|---|
| `Program Name` | Name of the program | MBA Batch 12 |
| `Session Topic` | Topic of the session | Marketing Strategy |
| `Moderator Name` | Name shown in Zoom | upGrad Monitor |
| `Session Start Time` | Time in HH:MM AM/PM format | 02:00 PM |
| `Meeting link` | Full Zoom meeting URL | https://zoom.us/j/123456 |
| `Host Email` | (Optional) Zoom host email | host@upgrad.com |
| `Host Password` | (Optional) Zoom host password | ••••••• |

> ⚠️ **Never share your CSV file publicly — it contains credentials.**
> The `.gitignore` in this project already prevents CSV files from being committed to GitHub.

A sample template is provided: [`sample_sessions.csv`](sample_sessions.csv)

---

## 🔧 Configuration

Open `zoom_command_center.py` and edit line 6 if needed:

```python
MAX_SESSIONS_PER_ACCOUNT = 2  # Set to 1 for Basic/Pro Zoom license
```

---

## 📁 Project Structure

```
zoom-command-center/
├── zoom_command_center.py   ← Main application
├── requirements.txt         ← Python dependencies
├── install.bat              ← One-time setup script
├── run_app.bat              ← Daily launcher
├── sample_sessions.csv      ← CSV template (no real data)
├── .gitignore               ← Prevents sensitive files from being committed
└── README.md                ← This file
```

---

## ❓ Troubleshooting

**"Python not found" during install**
→ Reinstall Python and make sure "Add Python to PATH" is checked

**App opens but browser doesn't launch**
→ Run `install.bat` again — Playwright browser may not have installed correctly

**Session stuck on "Launching..."**
→ Click the `↺ RETRY` button in the last column

**"Engine Not Ready" error**
→ Wait 5–10 seconds after opening the app for the browser engine to initialise

**Two people can't run webhooks simultaneously**
→ Only one machine should be the active operator per day (webhook feature — coming soon)

---

## 🔒 Security Notes

- Never commit your `.csv` files to GitHub — they contain Zoom credentials
- The `.gitignore` blocks CSV files automatically
- Host passwords are masked in the UI and never displayed

---

## 🛠️ Built With

- [Python](https://python.org) — Core language
- [Tkinter](https://docs.python.org/3/library/tkinter.html) — Desktop UI
- [Playwright](https://playwright.dev/python/) — Browser automation
- [Pandas](https://pandas.pydata.org/) — CSV processing

---

## 📬 Issues / Feedback

Raise an issue on GitHub or reach out to the MIS team directly.
