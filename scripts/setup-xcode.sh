#!/usr/bin/env bash
# Prepares a fresh clone for building PyQxel's native apps on a Mac: the Mac app, and the
# iPhone app with Xcode. Run it from anywhere in the repository:
#
#   scripts/setup-xcode.sh
#
# It checks for Xcode and installs what is missing of the rest: Homebrew's Node.js and
# XcodeGen, Rust with the Mac and iOS targets, and the web app's packages, including the
# Python and C++ runtimes the app bundles. Each step is skipped when already done, so it is
# safe to run again. It changes nothing outside this repository except those installs.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_MAJOR=24
RUST_TARGETS=(aarch64-apple-darwin x86_64-apple-darwin aarch64-apple-ios aarch64-apple-ios-sim)

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
ok() { printf '    %s\n' "$1"; }
fail() {
  printf '\n\033[31mStopped:\033[0m %s\n' "$1" >&2
  exit 1
}

[ "$(uname -s)" = "Darwin" ] || fail "Apple's tools run only on macOS."

step "Xcode"
developer_dir="$(xcode-select -p 2>/dev/null || true)"
if [[ "$developer_dir" != *Xcode*.app/* ]]; then
  fail "Install Xcode from the App Store, open it once to finish installing, then run:
  sudo xcode-select --switch /Applications/Xcode.app
and run this script again. (The Command Line Tools alone cannot build iOS apps.)"
fi
if ! xcodebuild -version > /dev/null 2>&1; then
  fail "Xcode's license has not been accepted. Run: sudo xcodebuild -license accept"
fi
ok "$(xcodebuild -version | head -1) at $developer_dir"
if xcrun --sdk iphonesimulator --show-sdk-path > /dev/null 2>&1; then
  ok "iOS platform installed"
else
  ok "The iOS platform is missing, so only the Mac app can be built. Add it in Xcode,"
  ok "Settings, Components, or with: xcodebuild -downloadPlatform iOS"
fi

step "Homebrew"
if ! command -v brew > /dev/null 2>&1; then
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$candidate" ] && eval "$("$candidate" shellenv)" && break
  done
fi
command -v brew > /dev/null 2>&1 || fail "Install Homebrew from https://brew.sh, then run this script again."
ok "$(brew --version | head -1)"

step "Node.js $NODE_MAJOR or newer"
node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [ "$node_major" -lt "$NODE_MAJOR" ]; then
  brew install node
  node_major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$node_major" -ge "$NODE_MAJOR" ] || fail "Node.js $NODE_MAJOR or newer is needed; found $(node -v)."
fi
ok "Node.js $(node -v)"

step "XcodeGen (makes the Xcode project from web/src-tauri/gen/apple/project.yml)"
command -v xcodegen > /dev/null 2>&1 || brew install xcodegen
ok "XcodeGen $(xcodegen --version | awk '{print $NF}')"

step "Rust"
export PATH="$HOME/.cargo/bin:$PATH"
if ! command -v rustup > /dev/null 2>&1; then
  ok "Installing Rust with rustup, Rust's official installer (https://rustup.rs)"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
fi
rustup target add "${RUST_TARGETS[@]}" > /dev/null
ok "$(rustc --version), targets: ${RUST_TARGETS[*]}"

step "The web app's packages, and the Python and C++ runtimes it bundles"
cd "$ROOT/web"
npm ci --no-audit --no-fund
PYODIDE_OFFLINE=1 npm run vendor > /dev/null
ok "Done"

step "Apple ID teams Xcode knows (for installing on an iPhone)"
teams="$(defaults read com.apple.dt.Xcode IDEProvisioningTeamByIdentifier 2> /dev/null |
  awk -F' = ' '/teamID/ {gsub(/[;"]/, "", $2); id = $2}
    /teamName/ {gsub(/[;"]/, "", $2); print "    " id "  " $2}' || true)"
if [ -n "$teams" ]; then
  echo "$teams"
else
  ok "None yet. To install on an iPhone, add your Apple ID in Xcode, Settings, Accounts,"
  ok "then run this script again to see its team ID."
fi

cat << EOF

$(printf '\033[1m')Ready.$(printf '\033[0m') In a new terminal (so Rust is on the PATH), from $ROOT/web:

  Mac app (.dmg):     npx tauri build --bundles dmg
  iPhone Simulator:   npx tauri ios run "iPhone 17 Pro"   (xcrun simctl list devices lists others)
  Your iPhone:        APPLE_DEVELOPMENT_TEAM=<team ID above> npx tauri ios run --release "<phone name>"

The README's Install section walks through each, including an iPhone's first-time setup.
EOF
