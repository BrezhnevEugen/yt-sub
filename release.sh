#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

VERSION="${YT_SUB_VERSION:-0.1.1}"
NOTARY_PROFILE="${YT_SUB_NOTARY_PROFILE:-brainai-notary}"
APP_DIR="dist/YT-sub.app"
DMG_PATH="dist/YT-sub-${VERSION}.dmg"
DMG_VOL="YT-sub ${VERSION}"

DO_RELEASE=0
SKIP_BUILD=0
for arg in "$@"; do
    case "$arg" in
        --release)     DO_RELEASE=1 ;;
        --skip-build)  SKIP_BUILD=1 ;;
        -h|--help)
            cat <<EOF
Usage: $0 [--release] [--skip-build]

Builds a signed and notarized YT-sub-VERSION.dmg under dist/.

  --release      After building, upload the DMG as a GitHub release.
  --skip-build   Reuse an existing dist/YT-sub.app (skip py2app step).

Env:
  YT_SUB_VERSION         default 0.1.0
  YT_SUB_NOTARY_PROFILE  notarytool keychain profile (default: $NOTARY_PROFILE)

Requires:
  - .venv with deps installed (run install.sh once)
  - Developer ID Application identity in keychain
  - notarytool keychain profile (run xcrun notarytool store-credentials once)
EOF
            exit 0 ;;
        *) echo "Unknown arg: $arg"; exit 2 ;;
    esac
done

step() { printf "\n\033[1;36m==> %s\033[0m\n" "$1"; }

SIGN_IDENTITY="$(security find-identity -v -p codesigning | grep 'Developer ID Application:' | sed -E 's/.*"([^"]+)".*/\1/' | head -1)"
if [ -z "$SIGN_IDENTITY" ]; then
    echo "No 'Developer ID Application:' identity in keychain. Aborting."
    exit 1
fi
echo "Signing identity: $SIGN_IDENTITY"

if [ "$SKIP_BUILD" -eq 0 ]; then
    step "py2app build"
    rm -rf build dist
    .venv/bin/python setup.py py2app 2>&1 | tail -3
fi

if [ ! -d "$APP_DIR" ]; then
    echo "Bundle not found at $APP_DIR"
    exit 1
fi
echo "Bundle size: $(du -sh "$APP_DIR" | cut -f1)"

step "Extracting native modules from python312.zip"
# py2app sometimes leaves .so/.dylib files inside the python lib archive
# (e.g. for namespace packages like 'google'). Notarization rejects any
# Mach-O binary inside a ZIP because each binary must be individually
# signed with a secure timestamp. Move them out alongside the archive.
PYZIP="$APP_DIR/Contents/Resources/lib/python312.zip"
PKG_ROOT="$APP_DIR/Contents/Resources/lib/python3.12"
if [ -f "$PYZIP" ]; then
    NATIVES=$(unzip -l "$PYZIP" 2>/dev/null | awk '/\.(so|dylib)$/ {print $NF}')
    if [ -n "$NATIVES" ]; then
        echo "$NATIVES" | while IFS= read -r rel; do
            [ -z "$rel" ] && continue
            target="$PKG_ROOT/$rel"
            mkdir -p "$(dirname "$target")"
            unzip -p "$PYZIP" "$rel" > "$target"
            chmod +x "$target"
            zip -d "$PYZIP" "$rel" >/dev/null
            echo "  extracted $rel"
        done
    else
        echo "  none — clean already"
    fi
fi

step "Code-signing the bundle (sign every Mach-O leaf first)"
# codesign --deep does not actually recurse into all Mach-O binaries.
# Apple's notarization needs every binary individually signed with a
# secure timestamp and hardened runtime — including .so/.dylib leaves
# AND extensionless executables (the embedded python, framework
# binaries, etc). Enumerate via `file` so nothing is missed.
LEAVES=$(find "$APP_DIR" -type f -not -path "*/Contents/MacOS/YT-sub" -exec file -h {} + 2>/dev/null \
    | awk -F: '/Mach-O/ {print $1}' \
    | sort -u)
LEAF_COUNT=$(echo "$LEAVES" | grep -c .)
echo "signing $LEAF_COUNT Mach-O leaves…"
echo "$LEAVES" | while IFS= read -r f; do
    [ -z "$f" ] && continue
    codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$f" 2>&1 | grep -v "replacing existing signature" || true
done
echo "signing the bundle…"
codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$APP_DIR"
codesign --verify --strict --verbose=2 "$APP_DIR" 2>&1 | tail -3

step "Notarizing the bundle"
TMP="$(mktemp -d)"
APP_ZIP="$TMP/YT-sub.zip"
/usr/bin/ditto -c -k --keepParent "$APP_DIR" "$APP_ZIP"
xcrun notarytool submit "$APP_ZIP" \
    --keychain-profile "$NOTARY_PROFILE" \
    --wait \
    --output-format json | tee "$TMP/notary-app.json"
APP_STATUS="$(/usr/bin/python3 -c '
import json, sys
print(json.load(open(sys.argv[1])).get("status",""))
' "$TMP/notary-app.json")"
if [ "$APP_STATUS" != "Accepted" ]; then
    echo "App notarization not Accepted: $APP_STATUS"
    exit 1
fi

step "Stapling app ticket"
xcrun stapler staple "$APP_DIR"
xcrun stapler validate "$APP_DIR"

step "Building DMG ($DMG_PATH)"
rm -f "$DMG_PATH"
DMG_STAGE="$(mktemp -d)/dmg-stage"
mkdir -p "$DMG_STAGE"
cp -R "$APP_DIR" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"

hdiutil create \
    -fs HFS+ \
    -volname "$DMG_VOL" \
    -srcfolder "$DMG_STAGE" \
    -format UDZO \
    -ov \
    "$DMG_PATH" >/dev/null
echo "DMG: $(du -h "$DMG_PATH" | cut -f1)"

step "Code-signing the DMG"
codesign --force --sign "$SIGN_IDENTITY" --timestamp "$DMG_PATH"

step "Notarizing the DMG"
xcrun notarytool submit "$DMG_PATH" \
    --keychain-profile "$NOTARY_PROFILE" \
    --wait \
    --output-format json | tee "$TMP/notary-dmg.json"
DMG_STATUS="$(/usr/bin/python3 -c '
import json, sys
print(json.load(open(sys.argv[1])).get("status",""))
' "$TMP/notary-dmg.json")"
if [ "$DMG_STATUS" != "Accepted" ]; then
    echo "DMG notarization not Accepted: $DMG_STATUS"
    exit 1
fi

step "Stapling DMG ticket"
xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH"
spctl --assess --type open --context context:primary-signature "$DMG_PATH" 2>&1
echo "DMG ready: $DMG_PATH"

if [ "$DO_RELEASE" -eq 1 ]; then
    step "Creating GitHub release v$VERSION"
    if gh release view "v$VERSION" >/dev/null 2>&1; then
        echo "Release v$VERSION already exists. Uploading DMG as asset…"
        gh release upload "v$VERSION" "$DMG_PATH" --clobber
    else
        gh release create "v$VERSION" "$DMG_PATH" \
            --title "YT-sub v$VERSION" \
            --notes "$(cat <<NOTES
Signed and notarized macOS bundle.

**Install:** download \`YT-sub-${VERSION}.dmg\`, open it, drag YT-sub.app into Applications.

**First-time setup inside the tray (🎬 in the menu bar):**
1. Load client_secret.json… — pick the OAuth JSON from Google Cloud Console (Desktop client, YouTube Data API v3 enabled, your email under "Test users" if consent screen is in Testing mode).
2. Sign in with Google.
3. Process URL… — paste any YouTube link.

**Wire AI agents** (one click each in the menu): Copy MCP config (Claude Desktop / Cursor), Install skill (~/.claude) (Claude Code), Install skill in project… (Claude Code / Cursor / Aider).

Source code: this repository. The DMG bundles Python and all dependencies, no install steps required.
NOTES
)"
    fi
fi

cat <<EOF

────────────────────────────────────────────────────────────────────
Done.

DMG:   $DMG_PATH ($(du -h "$DMG_PATH" | cut -f1))
SHA:   $(shasum -a 256 "$DMG_PATH" | cut -d' ' -f1)
EOF
[ "$DO_RELEASE" -eq 1 ] && echo "Release: $(gh release view "v$VERSION" --json url -q .url 2>/dev/null)"
echo "────────────────────────────────────────────────────────────────────"
