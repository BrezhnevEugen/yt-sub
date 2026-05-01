#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd -P)"
LABEL="com.brezhnev.yt-sub"
LAUNCH_AGENT="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_FILE="$HOME/Library/Logs/yt-sub.log"
APP_DIR="$PROJECT_DIR/dist/YT-sub.app"
APPS_INSTALLED="/Applications/YT-sub.app"

ENABLE_LOGIN=0
DO_UNINSTALL=0
SKIP_LAUNCH=0
DO_NOTARIZE=0
NOTARY_PROFILE="${YT_SUB_NOTARY_PROFILE:-yt-sub-notarize}"
SIGN_IDENTITY_OVERRIDE="${YT_SUB_SIGN_IDENTITY:-}"
for arg in "$@"; do
    case "$arg" in
        --login)        ENABLE_LOGIN=1 ;;
        --uninstall)    DO_UNINSTALL=1 ;;
        --no-launch)    SKIP_LAUNCH=1 ;;
        --notarize)     DO_NOTARIZE=1 ;;
        --notary-profile=*) NOTARY_PROFILE="${arg#--notary-profile=}" ;;
        --sign=*)       SIGN_IDENTITY_OVERRIDE="${arg#--sign=}" ;;
        -h|--help)
            cat <<EOF
Usage: $0 [--login] [--uninstall] [--no-launch] [--notarize]
          [--sign="Identity Name"] [--notary-profile=PROFILE]

Common:
  $0 --login                  Install + auto-start on every login.
  $0 --login --notarize       Same, plus full Apple notarization
                              (no Gatekeeper prompt anywhere).
  $0                          Install (manual start only).
  $0 --uninstall              Remove app + LaunchAgent.

The installer:
  - creates .venv and installs deps
  - generates app icon (menu-bar PNG + multi-res ICNS)
  - builds, code-signs and (optionally) notarizes YT-sub.app
  - copies it to /Applications
  - registers a per-user LaunchAgent (~/Library/LaunchAgents)
  - launches the tray (unless --no-launch)

Signing: auto-detects "Developer ID Application:" in the keychain.
Override with --sign or YT_SUB_SIGN_IDENTITY. Falls back to ad-hoc.

Notarization: requires a one-time keychain profile (default name
"$NOTARY_PROFILE"; override with --notary-profile or
YT_SUB_NOTARY_PROFILE). Create it once with:
  xcrun notarytool store-credentials $NOTARY_PROFILE \\
      --apple-id <your-apple-id>                       \\
      --team-id <TEAM_ID>                              \\
      --password <app-specific-password>
EOF
            exit 0 ;;
        *) echo "Unknown arg: $arg"; exit 2 ;;
    esac
done

step() { printf "\n\033[1;36m==> %s\033[0m\n" "$1"; }

if [ "$DO_UNINSTALL" -eq 1 ]; then
    step "Stopping LaunchAgent"
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || launchctl unload "$LAUNCH_AGENT" 2>/dev/null || true
    rm -f "$LAUNCH_AGENT"
    step "Removing /Applications/YT-sub.app"
    rm -rf "$APPS_INSTALLED"
    pkill -f "$PROJECT_DIR/.venv/bin/python.*app\.py" 2>/dev/null || true
    echo "Uninstalled. Project dir and ~/.config/yt-sub are untouched."
    exit 0
fi

step "Checking Python (need 3.10+)"
PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "python3 not found. Install via: xcode-select --install"
    exit 1
fi
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo "Python 3.10+ required. Got: $("$PYTHON_BIN" --version)"
    exit 1
}
echo "ok: $("$PYTHON_BIN" --version)"

step "Creating virtualenv (.venv)"
[ -d .venv ] || "$PYTHON_BIN" -m venv .venv
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements.txt --quiet
echo "ok"

step "Generating icons"
.venv/bin/python - <<'PY'
from icon import ensure_icon, ensure_icns
print("menu-bar:", ensure_icon())
print("app:     ", ensure_icns())
PY

step "Stopping any previous instance"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || launchctl unload "$LAUNCH_AGENT" 2>/dev/null || true
pkill -f "$PROJECT_DIR/.venv/bin/python.*app\.py" 2>/dev/null || true
sleep 0.5

step "Building YT-sub.app"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>YT-sub</string>
  <key>CFBundleDisplayName</key><string>YT-sub</string>
  <key>CFBundleIdentifier</key><string>$LABEL</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleExecutable</key><string>YT-sub</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

# .app launcher converges on the LaunchAgent so we never have two
# instances no matter how the user starts it.
cat > "$APP_DIR/Contents/MacOS/YT-sub" <<EOF
#!/bin/bash
LABEL="$LABEL"
PLIST="\$HOME/Library/LaunchAgents/\$LABEL.plist"
if ! launchctl print "gui/\$UID/\$LABEL" >/dev/null 2>&1; then
    [ -f "\$PLIST" ] && launchctl bootstrap "gui/\$UID" "\$PLIST" 2>/dev/null || true
fi
if launchctl print "gui/\$UID/\$LABEL" >/dev/null 2>&1; then
    exec launchctl kickstart "gui/\$UID/\$LABEL"
else
    # Fallback: run directly (e.g. project moved, plist missing)
    exec "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/app.py"
fi
EOF
chmod +x "$APP_DIR/Contents/MacOS/YT-sub"

cp "$PROJECT_DIR/assets/yt_icon.icns" "$APP_DIR/Contents/Resources/AppIcon.icns"
plutil -lint "$APP_DIR/Contents/Info.plist" >/dev/null
echo "ok: $APP_DIR"

step "Code-signing"
SIGN_IDENTITY=""
if [ -n "$SIGN_IDENTITY_OVERRIDE" ]; then
    SIGN_IDENTITY="$SIGN_IDENTITY_OVERRIDE"
else
    SIGN_IDENTITY="$(security find-identity -v -p codesigning | grep 'Developer ID Application:' | sed -E 's/.*"([^"]+)".*/\1/' | head -1)"
fi
if [ -n "$SIGN_IDENTITY" ]; then
    codesign --force --deep --options runtime --sign "$SIGN_IDENTITY" "$APP_DIR" 2>&1
    echo "signed with: $SIGN_IDENTITY"
else
    codesign --force --deep --sign - "$APP_DIR" 2>&1
    echo "WARN: no Developer ID found, used ad-hoc signature"
    echo "      first launch must be right-click → Open in Finder"
fi
codesign --verify --strict --verbose=2 "$APP_DIR" 2>&1 | tail -3
spctl --assess --type exec "$APP_DIR" 2>&1 || true

if [ "$DO_NOTARIZE" -eq 1 ]; then
    step "Notarizing (Apple scan, takes 1-5 minutes)"
    if [ -z "$SIGN_IDENTITY" ] || [ "$SIGN_IDENTITY" = "-" ]; then
        echo "Cannot notarize: app is not signed with a Developer ID."
        exit 1
    fi
    if ! command -v xcrun >/dev/null; then
        echo "xcrun not found. Install Xcode Command Line Tools."
        exit 1
    fi

    NOTARY_TMP="$(mktemp -d)"
    NOTARY_ZIP="$NOTARY_TMP/YT-sub.zip"
    /usr/bin/ditto -c -k --keepParent "$APP_DIR" "$NOTARY_ZIP"
    NOTARY_LOG="$NOTARY_TMP/result.json"

    if ! xcrun notarytool submit "$NOTARY_ZIP" \
        --keychain-profile "$NOTARY_PROFILE" \
        --wait \
        --output-format json > "$NOTARY_LOG" 2>&1; then
        echo "notarytool submit failed:"
        cat "$NOTARY_LOG"
        echo
        echo "If the keychain profile is missing, create it once with:"
        TEAM_ID="$(echo "$SIGN_IDENTITY" | sed -E 's/.*\(([A-Z0-9]+)\).*/\1/')"
        echo "  xcrun notarytool store-credentials $NOTARY_PROFILE \\"
        echo "      --apple-id <your-apple-id> \\"
        echo "      --team-id $TEAM_ID \\"
        echo "      --password <app-specific-password>"
        exit 1
    fi
    cat "$NOTARY_LOG"

    NOTARY_STATUS="$(/usr/bin/python3 -c '
import json, sys
print(json.load(open(sys.argv[1])).get("status",""))
' "$NOTARY_LOG")"
    if [ "$NOTARY_STATUS" != "Accepted" ]; then
        echo "Notarization not Accepted (got: $NOTARY_STATUS). Aborting."
        exit 1
    fi

    step "Stapling notarization ticket"
    xcrun stapler staple "$APP_DIR"
    xcrun stapler validate "$APP_DIR"
    spctl --assess --type exec "$APP_DIR" 2>&1
fi

step "Installing into /Applications"
rm -rf "$APPS_INSTALLED"
cp -R "$APP_DIR" "$APPS_INSTALLED"
/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister -f "$APPS_INSTALLED" 2>/dev/null || true
echo "ok: $APPS_INSTALLED"

step "Writing LaunchAgent"
mkdir -p "$(dirname "$LAUNCH_AGENT")" "$(dirname "$LOG_FILE")"
RUN_AT_LOAD="$([ "$ENABLE_LOGIN" -eq 1 ] && echo '<true/>' || echo '<false/>')"

cat > "$LAUNCH_AGENT" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROJECT_DIR/.venv/bin/python</string>
    <string>$PROJECT_DIR/app.py</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>RunAtLoad</key>$RUN_AT_LOAD
  <key>KeepAlive</key>
  <dict>
    <key>Crashed</key><true/>
    <key>SuccessfulExit</key><false/>
  </dict>
  <key>StandardOutPath</key><string>$LOG_FILE</string>
  <key>StandardErrorPath</key><string>$LOG_FILE</string>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
EOF
echo "ok: $LAUNCH_AGENT"

step "Loading LaunchAgent"
launchctl bootstrap "gui/$UID" "$LAUNCH_AGENT" 2>/dev/null || launchctl load "$LAUNCH_AGENT"
echo "ok"

if [ "$SKIP_LAUNCH" -eq 0 ]; then
    step "Starting"
    launchctl kickstart "gui/$UID/$LABEL"
    for i in 1 2 3 4 5; do
        sleep 1
        if pgrep -f "$PROJECT_DIR/.venv/bin/python.*app\.py" >/dev/null; then
            echo "ok (took ${i}s) — look for the red ▶ icon in the menu bar"
            break
        fi
        if [ "$i" -eq 5 ]; then
            echo "WARN: process not running after 5s. Tail of log:"
            tail -20 "$LOG_FILE" 2>/dev/null || true
        fi
    done
fi

cat <<EOF

────────────────────────────────────────────────────────────────────
Done.

App bundle:       $APPS_INSTALLED
LaunchAgent:      $LAUNCH_AGENT
Log:              $LOG_FILE
Auto-start:       $([ $ENABLE_LOGIN -eq 1 ] && echo "ENABLED on login" || echo "disabled (rerun with --login)")

How to launch:
  - Finder → Applications → YT-sub          (double-click; first
    launch may show "macOS cannot verify..." prompt — click Open)
  - launchctl kickstart gui/$UID/$LABEL     (CLI, always works)

Manual control:
  launchctl kickstart -k gui/$UID/$LABEL    # restart
  launchctl bootout gui/$UID/$LABEL         # stop
  $0 --uninstall                            # remove

First-time setup inside the tray:
  1. 🎬 → "Load client_secret.json…"  → pick OAuth JSON from Cloud
     Console (Desktop client, YouTube Data API v3 enabled, your email
     under "Test users" if consent screen is in Testing mode).
  2. "Sign in with Google".
  3. "Process URL…" with any YouTube link.

Wire AI agents (one click each in the menu):
  - "Copy MCP config"            → Claude Desktop / Cursor / etc.
  - "Install skill (~/.claude)"  → Claude Code (user-global).
  - "Install skill in project…"  → Claude Code / Cursor / Aider.
────────────────────────────────────────────────────────────────────
EOF
