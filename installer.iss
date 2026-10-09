; installer.iss
; ---------------
; Inno Setup script (https://jrsoftware.org/isinfo.php — free, the
; standard tool for a proper Windows installer wizard). Compiles to a
; single installer .exe that lets the user pick the install folder,
; offers a desktop shortcut, and adds a Start Menu entry.
;
; THE BROWSER (Chromium) IS NOT IN THE EXE — THIS INSTALLER CARRIES IT.
; It is copied once to
;   %LOCALAPPDATA%\Command Center\chromium\chromium-<rev>\chrome-win64\
; the folder the app launches it from. That keeps the
; auto-update exe small (~155 MB instead of ~435 MB). The app downloads the
; browser itself if that folder is ever missing (browser_setup.py), so
; skipping it here is never fatal — just a bigger first run.
;
; Before compiling, run `python tools/prepare_release_assets.py` once per
; Chromium revision: it writes installer_paths.iss (ChromiumRev,
; ChromiumSrcDir) which is included below. The browser copy is skipped when
; that revision is already installed, so re-installs/upgrades are fast.
;
; TEST COMPILES: `ISCC /DTestBuild installer.iss` builds an installer with its
; own AppId, install folder and browser folder, so trying it out can never
; touch (or register over) a real installation.

#define MyAppName "Zoom + Prism Command Center"
#define MyAppVersion "2.5.1"
#define MyAppPublisher "upGrad Education"
#define MyAppExeName "Zoom_Ai_latest.exe"

#include "installer_paths.iss"

#ifdef TestBuild
  #define BrowserRoot "{localappdata}\Command Center TEST\chromium"
#else
  #define BrowserRoot "{localappdata}\Command Center\chromium"
#endif

[Setup]
; This GUID identifies the app across versions — GENERATE ONCE, then
; never change it. Reusing the same AppId is what makes "install v2.1
; over v2.0" work as an upgrade instead of a broken side-by-side install.
; Generate your own via Tools -> Generate GUID in the Inno Setup IDE.
#ifdef TestBuild
AppId={{8F3B2C1A-9D4E-4A6F-B7C8-00000000TE57}
AppName={#MyAppName} (TEST)
#else
AppId={{8F3B2C1A-9D4E-4A6F-B7C8-1234567890AB}
AppName={#MyAppName}
#endif
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; lowest = always installs per-user (no admin prompt, no "install for
; all users?" choice) into {localappdata}\Programs\... — no
; PrivilegesRequiredOverridesAllowed line, so Inno Setup never shows
; its Install Mode page letting the installer be re-run "for all users."
PrivilegesRequired=lowest
#ifdef TestBuild
DefaultDirName={localappdata}\Programs\{#MyAppName} TEST
#else
DefaultDirName={localappdata}\Programs\{#MyAppName}
#endif
#ifdef TestBuild
DefaultGroupName={#MyAppName} TEST
#else
DefaultGroupName={#MyAppName}
#endif
; DisableDirPage=no (the default) is what gives you the "choose install
; folder" wizard page — don't set this to "yes" or that page disappears.
DisableDirPage=no
DisableProgramGroupPage=yes
#ifdef TestBuild
OutputBaseFilename=TEST_CommandCenter_Setup_v{#MyAppVersion}
#else
OutputBaseFilename=CommandCenter_Setup_v{#MyAppVersion}
#endif
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
; The browser, straight from this build machine's Playwright install (see the
; header note). Skipped when that revision is already on this computer.
Source: "{#ChromiumSrcDir}\*"; DestDir: "{#BrowserRoot}\chromium-{#ChromiumRev}\chrome-win64"; \
    Flags: recursesubdirs createallsubdirs ignoreversion; Check: BrowserMissing

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Nothing to install post-copy (the browser is copied by [Files] above).
; Just offer to launch the app.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up updater leftovers (see updater.py's _do_update) so a
; reinstall doesn't find a stale .bat/.new file sitting in {app}.
Type: files; Name: "{app}\*_updater.bat"
Type: files; Name: "{app}\*.new"
; The browser lives here (installed above, or downloaded by the app) so it
; runs from a fixed path (keeps Windows Firewall from prompting on every
; launch). ~395 MB — don't leave it behind on uninstall.
Type: filesandordirs; Name: "{#BrowserRoot}"

[Code]
{ Decided ONCE, before any file is copied. Checking per file would flip to
  "installed" as soon as chrome.exe itself lands, skipping every file after it
  (the first test install copied only 4 of 308 files for exactly that reason). }
var
  BrowserWasMissing: Boolean;

function InitializeSetup: Boolean;
begin
  BrowserWasMissing := not FileExists(ExpandConstant('{#BrowserRoot}\chromium-{#ChromiumRev}\chrome-win64\chrome.exe'));
  Result := True;
end;

function BrowserMissing: Boolean;
begin
  Result := BrowserWasMissing;
end;
