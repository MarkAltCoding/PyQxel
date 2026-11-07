#!/usr/bin/env bash
# Publish a new version of the Mac app: set the version everywhere it is written, commit,
# tag, and push. The tag starts the Release workflow (.github/workflows/release.yml), which
# builds the .dmg and the signed update and attaches them to a GitHub release; installed
# apps then offer "Update and restart" within a few hours, or on their next launch.
#
#   scripts/release.sh 0.2.0
#
# It refuses a version that is not newer than the current one, uncommitted changes, and
# any branch but main, so a release is always exactly what main holds.

set -euo pipefail

cd "$(dirname "$0")/.."
version="${1:-}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Usage: scripts/release.sh MAJOR.MINOR.PATCH, e.g. scripts/release.sh 0.2.0" >&2
    exit 1
fi

current="$(python3 -c 'import json; print(json.load(open("web/src-tauri/tauri.conf.json"))["version"])')"
newer="$(python3 -c '
import sys
parse = lambda text: tuple(int(part) for part in text.split("."))
print(parse(sys.argv[1]) > parse(sys.argv[2]))' "$version" "$current")"
if [[ "$newer" != True ]]; then
    echo "$version is not newer than the current version, $current." >&2
    exit 1
fi
if [[ "$(git branch --show-current)" != main ]]; then
    echo "Release from main." >&2
    exit 1
fi
# The editor settings file is kept local and may differ; anything else must be committed.
if [[ -n "$(git status --porcelain -- . ':!.vscode/settings.json')" ]]; then
    echo "Commit or stash your changes first." >&2
    exit 1
fi
if git rev-parse "v$version" >/dev/null 2>&1; then
    echo "The tag v$version already exists." >&2
    exit 1
fi
git pull --ff-only --quiet

# The app's version (tauri.conf.json, which the updater compares), the Rust package's, and
# the server's, which the health checks report.
python3 - "$version" <<'PY'
import re, sys
version = sys.argv[1]
# Each file is edited in place, keeping its formatting.
for path, pattern, replacement in [
    ("web/src-tauri/tauri.conf.json", r'(?m)^  "version": "[^"]+"', f'  "version": "{version}"'),
    ("web/src-tauri/Cargo.toml", r'(?m)^version = "[^"]+"', f'version = "{version}"'),
    ("web/src-tauri/Cargo.lock", r'(name = "pyqxel"\nversion = )"[^"]+"', rf'\g<1>"{version}"'),
    ("app/version.py", r'__version__: str = "[^"]+"', f'__version__: str = "{version}"'),
]:
    text = open(path).read()
    updated, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        sys.exit(f"Could not find the version in {path}.")
    open(path, "w").write(updated)
PY

git add web/src-tauri/tauri.conf.json web/src-tauri/Cargo.toml web/src-tauri/Cargo.lock app/version.py
git commit --quiet -m "Release $version"
git tag "v$version"
git push --quiet origin main "v$version"

echo "Released $version. The build takes about 15 minutes; follow it with:"
echo "  gh run list --workflow release.yml --limit 1"
