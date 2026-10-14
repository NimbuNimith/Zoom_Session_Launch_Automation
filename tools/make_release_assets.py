"""
make_release_assets.py
-----------------------
Run AFTER PyInstaller built dist\\Zoom_Ai_latest\\ and BEFORE compiling the installer
(build_release.bat does both in order). It:

  1. computes the RUNTIME ID - a hash of everything in dist\\Zoom_Ai_latest\\_internal
     (Python, Qt, the Playwright driver...) - and writes it to dist\\Zoom_Ai_latest\\runtime.id
     so the installer and the pack carry it. The app compares it with the one in
     update.json: same id -> an update only needs the new exe (~12 MB), different id
     -> it downloads the full pack.
  2. writes dist\\release\\ :
        Zoom_Ai_latest.exe                 the exe-only update
        CommandCenter_Pack_v<ver>.zip      the full pack (exe + _internal + runtime.id)
        update.json                        what the app reads (version, runtime id, URLs, SHA-256)

Publish order (see the release checklist in build_release.bat): create the GitHub release
v<ver> with the two assets FIRST, verify the downloads, and only then commit update.json
to the repository root - the moment it lands on main, running apps are offered the update.
version.txt is NOT touched (it stays 1.0.0 for the old single-exe versions).

    python tools/make_release_assets.py --notes "What changed, one line per item"
"""

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist", "Zoom_Ai_latest")
OUT = os.path.join(ROOT, "dist", "release")
REPO = "NimbuNimith/Zoom_Session_Launch_Automation"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def runtime_id(folder: str) -> str:
    """Hash of every file under _internal (relative path + content). Anything that changes
    what the exe runs ON - a new Python, Qt, driver or library - changes it."""
    internal = os.path.join(folder, "_internal")
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(internal):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, internal).replace("\\", "/")
            h.update(rel.encode("utf-8") + b"\0" + sha256_file(full).encode("ascii") + b"\n")
    return h.hexdigest()[:32]


def current_version() -> str:
    text = open(os.path.join(ROOT, "updater.py"), encoding="utf-8").read()
    return re.search(r'^CURRENT_VERSION\s*=\s*"([^"]+)"', text, re.M).group(1)


def make_pack(folder: str, zip_path: str):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                z.write(full, os.path.relpath(full, folder).replace("\\", "/"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", default="", help="release notes shown in the update prompt")
    args = ap.parse_args()

    exe = os.path.join(DIST, "Zoom_Ai_latest.exe")
    if not os.path.isfile(exe) or not os.path.isdir(os.path.join(DIST, "_internal")):
        sys.exit(f"No build found at {DIST} - run PyInstaller first.")

    version = current_version()
    rid = runtime_id(DIST)
    with open(os.path.join(DIST, "runtime.id"), "w", encoding="ascii") as f:
        f.write(rid)
    print(f"version {version}   runtime id {rid}")

    os.makedirs(OUT, exist_ok=True)
    for name in os.listdir(OUT):
        os.remove(os.path.join(OUT, name))

    exe_asset = "Zoom_Ai_latest.exe"
    pack_asset = f"CommandCenter_Pack_v{version}.zip"
    with open(exe, "rb") as src, open(os.path.join(OUT, exe_asset), "wb") as dst:
        dst.write(src.read())
    make_pack(DIST, os.path.join(OUT, pack_asset))

    base = f"https://github.com/{REPO}/releases/download/v{version}"
    manifest = {
        "version": version,
        "runtime_id": rid,
        "notes": args.notes,
        "exe": {"url": f"{base}/{exe_asset}",
                "sha256": sha256_file(os.path.join(OUT, exe_asset)),
                "size": os.path.getsize(os.path.join(OUT, exe_asset))},
        "pack": {"url": f"{base}/{pack_asset}",
                 "sha256": sha256_file(os.path.join(OUT, pack_asset)),
                 "size": os.path.getsize(os.path.join(OUT, pack_asset))},
    }
    with open(os.path.join(OUT, "update.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    mb = lambda n: f"{n / 1048576:.1f} MB"
    print(f"\n{OUT}")
    print(f"  {exe_asset:34s} {mb(manifest['exe']['size'])}")
    print(f"  {pack_asset:34s} {mb(manifest['pack']['size'])}")
    print("  update.json")
    print("\nNext: create the release and upload the two assets, verify the download URLs,")
    print("then copy dist\\release\\update.json to the repository root and push it LAST:")
    print(f"  gh release create v{version} dist\\release\\{exe_asset} dist\\release\\{pack_asset} --title v{version}")


if __name__ == "__main__":
    main()
