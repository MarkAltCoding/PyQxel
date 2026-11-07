#!/usr/bin/env bash
# Build PyQxel for your iPhone and install it, in one step: the way to update the iPhone
# app until it is on the App Store, and to renew the 7 days a free Apple ID's install
# lasts. Plug the phone in and unlock it, then:
#
#   APPLE_DEVELOPMENT_TEAM=YOURTEAMID IPHONE=00008120-... scripts/install-iphone.sh
#
# APPLE_DEVELOPMENT_TEAM is your team ID (scripts/setup-xcode.sh prints it). IPHONE is the
# phone's identifier from `xcrun devicectl list devices`; when unset and only one phone is
# connected, that one is used. Your sign-in and keys stay on the phone across installs.

set -euo pipefail

TEAM="${APPLE_DEVELOPMENT_TEAM:?Set APPLE_DEVELOPMENT_TEAM to the team ID scripts/setup-xcode.sh prints.}"
BUNDLE_ID="com.markaltcoding.pyqxel"
export PATH="$HOME/.cargo/bin:/opt/homebrew/bin:$PATH"
cd "$(dirname "$0")/../web"

device="${IPHONE:-}"
if [[ -z "$device" ]]; then
    # Physical iPhones that are connected, from devicectl's table (simulators say "simulated").
    phones="$(xcrun devicectl list devices 2>/dev/null | grep -i 'iphone' | grep -v -i 'simulated' \
        | grep -v -i 'unavailable' | grep -i -E 'available|connected' | awk '{for (i = 1; i <= NF; i++) if ($i ~ /^[0-9A-F]{8}-[0-9A-F-]+$/) print $i}' || true)"
    count="$(printf '%s' "$phones" | grep -c . || true)"
    if [[ "$count" != 1 ]]; then
        echo "Connect one iPhone, unlocked, or set IPHONE (see: xcrun devicectl list devices)." >&2
        exit 1
    fi
    device="$phones"
fi

# Building sets the team in the Xcode project; the repository keeps it blank, so it is put
# back however the build ends.
trap 'git checkout --quiet -- src-tauri/gen/apple/pyqxel.xcodeproj/project.pbxproj 2>/dev/null || true' EXIT

echo "==> Building PyQxel for the iPhone (a few minutes)"
# A free Apple ID's build fails at its last step, exporting an .ipa, after the signed app
# is built, so the result is checked rather than the exit status.
APPLE_DEVELOPMENT_TEAM="$TEAM" npx tauri ios build --target aarch64 || true
app="$(ls -dt ~/Library/Developer/Xcode/DerivedData/pyqxel-*/Build/Products/release-iphoneos/PyQxel.app 2>/dev/null | head -1)"
if [[ -z "$app" || "$(find "$app" -maxdepth 0 -mmin -30)" == "" ]]; then
    echo "The build did not produce a fresh PyQxel.app; see the errors above." >&2
    exit 1
fi

echo "==> Installing on $device"
xcrun devicectl device install app --device "$device" "$app"
xcrun devicectl device process launch --device "$device" "$BUNDLE_ID" >/dev/null
echo "Installed and opened. A free Apple ID's install lasts 7 days; run this again to renew it."
