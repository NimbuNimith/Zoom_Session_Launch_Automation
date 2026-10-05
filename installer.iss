; installer.iss
; ---------------
; Inno Setup script (https://jrsoftware.org/isinfo.php — free, the
; standard tool for a proper Windows installer wizard). Compiles to a
; single installer .exe that lets the user pick the install folder,
; offers a desktop shortcut, and adds a Start Menu entry.
;
; NOTE: the "installplaywright" task/Run entry that used to run
; `Zoom_Ai_latest.exe --playwright-install` after copying files is
; gone. Chromium is now bundled directly into Zoom_Ai_latest.exe (see
; that file's .spec for why), so there's no separate browser-install
; step left to run — the moment the exe is copied to {app}, everything
; it needs is already inside it.

#define MyAppName "Zoom + Prism Command Center"
#define MyAppVersion "2.1.17"
#define MyAppPublisher "upGrad Education"
#define MyAppExeName "Zoom_Ai_latest.exe"

[Setup]
; This GUID identifies the app across versions — GENERATE ONCE, then
; never change it. Reusing the same AppId is what makes "install v2.1
; over v2.0" work as an upgrade instead of a broken side-by-side install.
; Generate your own via Tools -> Generate GUID in the Inno Setup IDE.
AppId={{8F3B2C1A-9D4E-4A6F-B7C8-1234567890AB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; lowest = always installs per-user (no admin prompt, no "install for
; all users?" choice) into {localappdata}\Programs\... — no
; PrivilegesRequiredOverridesAllowed line, so Inno Setup never shows
; its Install Mode page letting the installer be re-run "for all users."
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
; DisableDirPage=no (the default) is what gives you the "choose install
; folder" wizard page — don't set this to "yes" or that page disappears.
DisableDirPage=no
DisableProgramGroupPage=yes
OutputBaseFilename=CommandCenter_Setup_v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Zoom_Ai_latest.spec builds onefile (a.binaries/a.datas passed directly
; into EXE(), no separate COLLECT() step) — so this is a single file,
; dist\Zoom_Ai_latest.exe, not a dist\Zoom_Ai_latest\ folder. Confirmed
; against an actual build log: "Copying bootloader EXE to
; ...\dist\Zoom_Ai_latest.exe" — no subfolder in that path. An earlier
; version of this file assumed a folder here, which would have failed
; to compile (Inno Setup errors on a Source pattern matching zero files).
Source: "dist\Zoom_Ai_latest.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Chromium ships inside Zoom_Ai_latest.exe now — nothing to install
; post-copy. Just offer to launch the app.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up updater leftovers (see updater.py's _do_update) so a
; reinstall doesn't find a stale .bat/.new file sitting in {app}.
Type: files; Name: "{app}\*_updater.bat"
Type: files; Name: "{app}\*.new"
