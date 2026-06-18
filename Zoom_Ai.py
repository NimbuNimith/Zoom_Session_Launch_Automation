import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
import asyncio
import threading
import traceback
import random
from datetime import datetime
from playwright.async_api import async_playwright
import re
import sys
import os
import subprocess

# ─────────────────────────────────────────────
# AUTO-UPDATER
# ─────────────────────────────────────────────
CURRENT_VERSION = "1.0.0"
VERSION_URL  = "https://raw.githubusercontent.com/NimbuNimith/Zoom_Session_Launch_Automation/main/version.txt"
DOWNLOAD_URL = "https://github.com/NimbuNimith/Zoom_Session_Launch_Automation/releases/latest/download/Zoom_Ai_latest.exe"

def _do_update(new_version):
    """Downloads the new exe and writes a .bat to replace + relaunch, then exits."""
    current_exe = sys.executable if getattr(sys, "frozen", False) else None
    if not current_exe:
        # Running as a plain .py — just inform, no self-replace needed
        messagebox.showinfo(
            "Update Available",
            f"Version {new_version} is available.\n\n"
            "Download the latest release from GitHub and replace this file."
        )
        return

    new_exe_path = current_exe + ".new"
    try:
        import requests
        messagebox.showinfo(
            "Downloading Update",
            f"Downloading v{new_version}...\nThe app will restart automatically when done."
        )
        r = requests.get(DOWNLOAD_URL, stream=True, timeout=60)
        r.raise_for_status()
        with open(new_exe_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception as e:
        messagebox.showerror("Update Failed", f"Could not download update:\n{e}")
        return

    # Write a small .bat: waits for this process to exit, replaces the exe,
    # relaunches it, then deletes itself.
    bat_path = current_exe + "_updater.bat"
    bat = (
        "@echo off\n"
        "timeout /t 2 /nobreak > nul\n"
        f'move /y "{new_exe_path}" "{current_exe}"\n'
        f'start "" "{current_exe}"\n'
        'del "%~f0"\n'
    )
    with open(bat_path, "w") as f:
        f.write(bat)

    subprocess.Popen(bat_path, shell=True)
    sys.exit(0)


def check_for_update():
    """
    Runs in a background thread at startup.
    Silently skips if offline or version check fails — never blocks the app.
    """
    try:
        import requests
        from packaging import version as pkg_version
        resp = requests.get(VERSION_URL, timeout=5)
        resp.raise_for_status()
        latest = resp.text.strip()
        if pkg_version.parse(latest) > pkg_version.parse(CURRENT_VERSION):
            # Must touch Tkinter from the main thread only
            def _prompt():
                if messagebox.askyesno(
                    "Update Available",
                    f"A new version is available: v{latest}\n"
                    f"You have: v{CURRENT_VERSION}\n\n"
                    "Download and restart now?"
                ):
                    _do_update(latest)
            root.after(1000, _prompt)
    except Exception:
        pass  # Offline or GitHub unreachable — fail silently


# --- CONFIGURATION ---
MAX_SESSIONS_PER_ACCOUNT = 2  # Set to 1 if you only have a Basic/Pro license

# --- GLOBAL STATE ---
session_store = {}
running_sessions = set()       
active_accounts = {}           
loop = None
browser_ready = False
browser_instance = None
playwright_instance = None

class ZoomControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("🚀 Zoom Command Center (V40 - Manual Retry Logic)")
        self.root.geometry("1450x600") # Made wider for new column
        
        self.df = pd.DataFrame()
        
        # --- UI LAYOUT ---
        toolbar = tk.Frame(root, pady=10)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        
        self.btn_load = tk.Button(toolbar, text="📂 Upload CSV", command=self.load_csv, bg="#ddd")
        self.btn_load.pack(side=tk.LEFT, padx=10)
        
        self.btn_add = tk.Button(toolbar, text="➕ Add Session", command=self.open_add_session_dialog, bg="#d9ffcc")
        self.btn_add.pack(side=tk.LEFT, padx=10)
        
        self.btn_auto = tk.Button(toolbar, text="🤖 Start Auto-Pilot", command=self.toggle_auto_pilot, bg="#f0f0f0", state=tk.DISABLED)
        self.btn_auto.pack(side=tk.LEFT, padx=10)
        
        self.lbl_status = tk.Label(toolbar, text="Initializing Engine...", fg="orange")
        self.lbl_status.pack(side=tk.RIGHT, padx=10)

        # Main Table - Added 'retry' column
        columns = ("prog", "topic", "mod", "time", "role", "status", "action", "retry")
        self.tree = ttk.Treeview(root, columns=columns, show="headings", height=20)
        
        self.tree.heading("prog", text="Program")
        self.tree.heading("topic", text="Topic")
        self.tree.heading("mod", text="Moderator")
        self.tree.heading("time", text="Start Time")
        self.tree.heading("role", text="Role")
        self.tree.heading("status", text="Status")
        self.tree.heading("action", text="Action")
        self.tree.heading("retry", text="Retry") # New Header
        
        self.tree.column("prog", width=150)
        self.tree.column("topic", width=200)
        self.tree.column("mod", width=100)
        self.tree.column("time", width=100)
        self.tree.column("role", width=80)
        self.tree.column("status", width=200)
        self.tree.column("action", width=120)
        self.tree.column("retry", width=100, anchor="center") # New Column
        
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Bind Single Click to handle the "Retry" button specifically
        self.tree.bind("<Button-1>", self.on_single_click)
        # Bind Double Click for standard Launch/End
        self.tree.bind("<Double-1>", self.on_row_double_click)

        self.auto_pilot_running = False
        self.start_async_loop()

    def load_csv(self):
        file_path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")])
        if not file_path: return
        try:
            self.df = pd.read_csv(file_path)
            self.df.columns = [c.strip() for c in self.df.columns]
            if 'Host Email' not in self.df.columns: self.df['Host Email'] = ""
            if 'Host Password' not in self.df.columns: self.df['Host Password'] = ""
            self.df.fillna("", inplace=True)
            
            for item in self.tree.get_children(): self.tree.delete(item)
            
            for index, row in self.df.iterrows():
                self.add_session_to_system(row)
            
            self.btn_auto.config(state=tk.NORMAL)
            self.lbl_status.config(text=f"Loaded {len(self.df)} sessions.", fg="black")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load CSV: {e}")

    def add_session_to_system(self, row_data):
        link = row_data['Meeting link']
        has_creds = len(str(row_data['Host Email'])) > 3
        role = "👑 HOST" if has_creds else "👤 Guest"
        if link in session_store: return

        # Default empty value for 'retry' column
        self.tree.insert("", tk.END, iid=link, values=(
            row_data['Program Name'], row_data['Session Topic'], row_data['Moderator Name'],
            row_data['Session Start Time'], role, "Pending", "▶ Launch", ""
        ))
        session_store[link] = { "data": row_data, "status": "Pending", "page": None }

    def open_add_session_dialog(self):
        popup = tk.Toplevel(self.root)
        popup.title("Add New Session")
        popup.geometry("400x550")
        
        fields = [("Program Name", "Quick Add"), ("Session Topic", "New Session"), 
                  ("Moderator Name", "Monitor"), ("Session Start Time", "02:00 PM"), 
                  ("Meeting link", ""), ("Host Email", ""), ("Host Password", "")]
        entries = {}
        for lbl_text, default in fields:
            tk.Label(popup, text=lbl_text, font=("Arial", 10, "bold")).pack(anchor="w", padx=20, pady=(10, 0))
            ent = tk.Entry(popup, width=40)
            ent.pack(padx=20, pady=5)
            ent.insert(0, default)
            entries[lbl_text] = ent
            
        def save_session():
            link = entries["Meeting link"].get().strip()
            if len(link) < 5:
                messagebox.showerror("Error", "Meeting Link is required!")
                return
            new_row = {k: entries[k].get() for k in entries}
            self.add_session_to_system(new_row)
            messagebox.showinfo("Success", "Session Added!")
            popup.destroy()
            self.btn_auto.config(state=tk.NORMAL)

        tk.Button(popup, text="💾 SAVE SESSION", bg="lightgreen", command=save_session).pack(pady=20, fill=tk.X, padx=20)

    def toggle_auto_pilot(self):
        if not self.auto_pilot_running:
            self.auto_pilot_running = True
            self.btn_auto.config(text="🛑 Stop Auto-Pilot", bg="#ffcccc")
            self.lbl_status.config(text="🤖 Auto-Pilot Starting in 3s...", fg="green")
            self.root.after(3000, self.run_auto_pilot_check)
        else:
            self.auto_pilot_running = False
            self.btn_auto.config(text="🤖 Start Auto-Pilot", bg="#f0f0f0")
            self.lbl_status.config(text="Auto-Pilot Stopped", fg="red")

    def parse_time_robust(self, time_str):
        time_str = str(time_str).strip().lower()
        if "am" in time_str and " am" not in time_str: time_str = time_str.replace("am", " am")
        if "pm" in time_str and " pm" not in time_str: time_str = time_str.replace("pm", " pm")
        formats = ["%I:%M %p", "%I:%M%p", "%H:%M", "%-I:%M %p"]
        for fmt in formats:
            try: return datetime.strptime(time_str, fmt)
            except: continue
        try:
            if len(time_str.split(":")[0]) == 1:
                return datetime.strptime("0" + time_str, "%I:%M %p")
        except: pass
        return None

    def run_auto_pilot_check(self):
        if not self.auto_pilot_running: return
        now = datetime.now()
        self.lbl_status.config(text=f"🤖 Scanning... ({now.strftime('%H:%M:%S')})", fg="green")
        
        for link, info in session_store.items():
            if info["status"] != "Pending": continue 
            try:
                start_str = str(info['data']['Session Start Time'])
                start_obj = self.parse_time_robust(start_str)
                if start_obj:
                    start_time = start_obj.replace(year=now.year, month=now.month, day=now.day)
                    diff = (start_time - now).total_seconds() / 60
                    if -30 <= diff <= 15:
                        self.launch_session_host(link)
            except: pass
        self.root.after(10000, self.run_auto_pilot_check)

    # --- CLICK HANDLERS ---
    def on_single_click(self, event):
        """Detects if user clicked the RETRY column."""
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell": return
        
        column = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)
        
        # Column #8 is the Retry column (returns '#8')
        if column == "#8" and row_id:
            val = self.tree.item(row_id, "values")[7] # Index 7 is Retry col
            if "RETRY" in val:
                self.force_retry_session(row_id)

    def on_row_double_click(self, event):
        """Standard Double Click for Launch/End."""
        try:
            item_id = self.tree.selection()[0]
            current_vals = self.tree.item(item_id, "values")
            action_text = current_vals[6]
            if "Launch" in action_text: self.launch_session_host(item_id)
            elif "End" in action_text: self.end_session_action(item_id)
            elif "JOIN" in action_text: self.join_session_guest(item_id)
        except: pass

    def force_retry_session(self, link):
        """Cleans up any blocks and forces a re-launch."""
        print(f"🔄 FORCE RETRYING {link}...")
        
        # 1. Clear status
        session_store[link]["status"] = "Pending"
        
        # 2. Force remove lock (just in case it got stuck)
        mid_match = re.search(r"/j/(\d+)|/s/(\d+)|/wc/(\d+)", link)
        mid = next((m for m in mid_match.groups() if m), None) if mid_match else link
        if mid in running_sessions: running_sessions.discard(mid)
        
        # 3. Update UI to look clean
        self.update_status(link, "Pending", "▶ Launch")
        
        # 4. Trigger Launch Immediately
        self.launch_session_host(link)

    def launch_session_host(self, link):
        if not browser_ready: return
        
        mid_match = re.search(r"/j/(\d+)|/s/(\d+)|/wc/(\d+)", link)
        mid = next((m for m in mid_match.groups() if m), None) if mid_match else link
        
        # Check locks (unless we just forced retry, which cleared it)
        if mid in running_sessions:
            print(f"⚠️ Blocked Duplicate Launch for {mid}")
            return
        
        # Account Limit Check
        email = str(session_store[link]['data'].get('Host Email', '')).strip()
        if len(email) > 3:
            current_count = len(active_accounts.get(email, set()))
            if current_count >= MAX_SESSIONS_PER_ACCOUNT:
                msg = f"⚠️ Account Full ({current_count}/{MAX_SESSIONS_PER_ACCOUNT})"
                self.update_status(link, msg, "Retry Later")
                return

        if session_store[link]["status"] != "Pending": return
        
        running_sessions.add(mid)
        if len(email) > 3:
            if email not in active_accounts: active_accounts[email] = set()
            active_accounts[email].add(mid)
        
        self.update_status(link, "🚀 Queued...", "...")
        session_store[link]["status"] = "Launching..." 
        data = session_store[link]['data']
        asyncio.run_coroutine_threadsafe(launch_browser_task(link, data, mode="HOST"), loop)

    def end_session_action(self, link):
        if messagebox.askyesno("Confirm End", "Are you sure you want to END this meeting?"):
            self.update_status(link, "🛑 Ending...", "...")
            asyncio.run_coroutine_threadsafe(end_meeting_task(link), loop)

    def join_session_guest(self, link):
        data = session_store[link]['data']
        asyncio.run_coroutine_threadsafe(launch_browser_task(link, data, mode="GUEST"), loop)

    def update_status(self, link, status, action_override=None):
        try:
            session_store[link]["status"] = status
            vals = list(self.tree.item(link, "values"))
            vals[5] = status
            
            # Action Column Logic
            if action_override: vals[6] = action_override
            else:
                if "LIVE" in status: vals[6] = "⏹ End Session"
                elif "Ended" in status: vals[6] = "Closed"
                elif "Pending" in status: vals[6] = "▶ Launch"
            
            # Retry Column Logic
            # If status contains warning/error/full/failed -> Show RETRY button
            if any(x in status for x in ["⚠️", "❌", "Failed", "Full", "Error"]):
                vals[7] = "↺ RETRY"
            else:
                vals[7] = ""

            tag = "live" if "LIVE" in status else "waiting" if "Waiting" in status else "err" if "⚠️" in status else ""
            self.tree.item(link, values=vals, tags=(tag,))
        except: pass

    def start_async_loop(self):
        global loop
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=self.run_async_loop, args=(loop,), daemon=True)
        t.start()
        self.tree.tag_configure('live', foreground='green', font=('Helvetica', 10, 'bold'))
        self.tree.tag_configure('waiting', foreground='orange', font=('Helvetica', 10, 'bold'))
        self.tree.tag_configure('err', foreground='red', font=('Helvetica', 10, 'bold'))

    def run_async_loop(self, loop):
        asyncio.set_event_loop(loop)
        loop.run_until_complete(setup_playwright())
        loop.run_forever()

# --- ASYNC ENGINE ---

async def setup_playwright():
    global playwright_instance, browser_instance, browser_ready
    print("⚙️ Engine Start...")
    try:
        playwright_instance = await async_playwright().start()
        browser_instance = await playwright_instance.chromium.launch(
            headless=False,
            args=["--deny-permission-prompts", "--window-size=1200,800"]
        )
        browser_ready = True
        print("✅ Engine Ready")
        app.lbl_status.config(text="Engine Ready", fg="green")
        asyncio.create_task(monitoring_heartbeat())
    except Exception as e:
        print(f"Engine Error: {e}")

async def cleanup_session(url):
    mid_match = re.search(r"/j/(\d+)|/s/(\d+)|/wc/(\d+)", url)
    mid = next((m for m in mid_match.groups() if m), None) if mid_match else None
    if mid: running_sessions.discard(mid)
    
    info = session_store.get(url)
    if info:
        email = str(info['data'].get('Host Email', '')).strip()
        if len(email) > 3 and mid:
            if email in active_accounts and mid in active_accounts[email]:
                active_accounts[email].discard(mid)
                print(f"🔓 Released slot for {email}")

async def end_meeting_task(url):
    info = session_store.get(url)
    if not info or not info["page"]: return
    try:
        page = info["page"]
        try: await page.click("button:has-text('End')", timeout=5000)
        except: pass
        await asyncio.sleep(0.5)
        await page.click("button:has-text('End Meeting for All')", timeout=5000)
        await page.close()
        await cleanup_session(url)
        app.update_status(url, "⚫ Ended", "Closed")
    except:
        await info["page"].close()
        await cleanup_session(url)
        app.update_status(url, "❌ Force Closed", "Closed")

async def launch_browser_task(url, data, mode="HOST"):
    delay = random.uniform(2, 6)
    print(f"⏳ Staggering launch for {delay:.1f}s...")
    await asyncio.sleep(delay)
    
    bot_name = data['Moderator Name']
    email = str(data.get('Host Email', '')).strip()
    password = str(data.get('Host Password', '')).strip()
    
    try:
        context = await browser_instance.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            permissions=["microphone", "camera"],
            ignore_https_errors=True
        )
        page = await context.new_page()
        if mode == "HOST": session_store[url]["page"] = page

        if mode == "HOST" and len(email) > 3:
            app.update_status(url, "🔑 Logging in...", "...")
            try:
                await page.goto("https://zoom.us/signin", timeout=100000, wait_until='domcontentloaded')
                if "profile" in page.url:
                    print("   ✅ Already logged in!")
                else: 
                    await page.click("input[name='email'], #email", timeout=100000)
                    await asyncio.sleep(0.5)
                    await page.fill("input[name='email'], #email", email)
                    
                    for attempt in range(3):
                        try: await page.click("button:has-text('Next')", timeout=100000)
                        except: pass
                        try:
                            await page.wait_for_selector("input[name='password'], #password", state="visible", timeout=100000)
                            break
                        except: await asyncio.sleep(2)
                    
                    await page.wait_for_selector("input[name='password'], #password", state="visible", timeout=100000)
                    await page.fill("input[name='password'], #password", password)
                    await page.click("button:has-text('Sign In'), button:has-text('Sign in')", timeout=100000)
                    await page.wait_for_url("**/profile", timeout=100000, wait_until='domcontentloaded')
                    print("   ✅ Login Success")
            except Exception as e:
                print(f"   ⚠️ Login Failed for {bot_name}: {e}")
                # We proceed anyway, sometimes cookies work

        if mode == "HOST": app.update_status(url, "🌍 Starting...", "...")
        mid_match = re.search(r"/j/(\d+)|/s/(\d+)|/wc/(\d+)", url)
        mid = next((m for m in mid_match.groups() if m), None)
        target_url = f"https://upgrad.zoom.us/wc/{mid}/start" if (mode=="HOST" and email) else f"https://upgrad.zoom.us/wc/{mid}/join"
        await page.goto(target_url, timeout=90000, wait_until='domcontentloaded')

        try:
            rescue = page.locator("text=Join from your browser")
            if await rescue.count() > 0: await rescue.click(force=True)
        except: pass

        try:
            name_input = page.locator("input#input-for-name, input#input-name").first
            if await name_input.is_visible(timeout=8000):
                await name_input.fill(bot_name)
                await page.click("button.preview-join-button")
        except: pass
        
        if mode == "HOST": app.update_status(url, "⏳ Connecting...", "...")

    except Exception as e:
        print(f"❌ Error {bot_name}: {e}")
        await cleanup_session(url)
        if mode == "HOST": app.update_status(url, "❌ Failed", "▶ Launch")

async def monitoring_heartbeat():
    while True:
        if not browser_ready:
            await asyncio.sleep(2)
            continue
        for link, info in session_store.items():
            page = info["page"]
            if not page or page.is_closed(): continue
            try:
                status = "Processing..."
                if await page.locator("button:has-text('End')").count() > 0: status = "🔴 LIVE"
                elif await page.locator("button:has-text('Leave')").count() > 0: status = "🔴 LIVE"
                else:
                    text = await page.inner_text("body")
                    t_low = text.lower()
                    if "waiting for the host" in t_low: status = "⏳ Waiting for Host"
                    elif "host has another meeting" in t_low: status = "⚠️ Host Collision"
                    elif "meeting has ended" in t_low: status = "⚫ Ended"
                    elif "join from your browser" in t_low:
                        status = "⚠️ Stuck (Popup)"
                        try: await page.click("text=Join from your browser", force=True)
                        except: pass
                if status != info["status"]:
                     action = "⏹ End Session" if "LIVE" in status else "..."
                     app.update_status(link, status, action)
            except: pass
        await asyncio.sleep(3)

if __name__ == "__main__":
    root = tk.Tk()
    app = ZoomControlApp(root)
    # Check for updates in background — never blocks startup
    threading.Thread(target=check_for_update, daemon=True).start()
    root.mainloop()