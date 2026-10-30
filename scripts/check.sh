#!/usr/bin/env bash
# Run the checks CI runs, quietly, and print only what fails.
#
#   scripts/check.sh          everything
#   scripts/check.sh py       the API's: formatting, lint, types, tests
#   scripts/check.sh web      the frontend's: types, lint, formatting, tests
#
# It first regenerates web/openapi.json and web/src/api/schema.d.ts from the API, so the
# frontend's types always match it, and formats the code, as CI then expects. Extra
# arguments go to pytest, e.g. scripts/check.sh py -k portfolio.

set -uo pipefail
cd "$(dirname "$0")/.."

part="${1:-all}"
[[ $# -gt 0 ]] && shift
python=venv/bin/python
failed=()

step() {
    local name="$1"
    shift
    local output
    if ! output="$("$@" 2>&1)"; then
        failed+=("$name")
        printf '✗ %s\n%s\n\n' "$name" "$(printf '%s\n' "$output" | tail -40)"
    else
        printf '✓ %s\n' "$name"
    fi
}

if [[ $part == all || $part == py || $part == web ]]; then
    step "OpenAPI schema and frontend types" bash -c \
        "$python -m app.jobs.openapi >/dev/null && cd web && npm run --silent gen:api >/dev/null"
fi

if [[ $part == all || $part == py ]]; then
    step "black" venv/bin/black -q app tests web/src/runners/python
    step "flake8" venv/bin/flake8 app tests web/src/runners/python
    step "mypy" venv/bin/mypy app tests web/src/runners/python
    step "pytest" bash -c "$python -m pytest -q -p no:cacheprovider $* 2>&1 | tail -15; exit \${PIPESTATUS[0]}"
fi

if [[ $part == all || $part == web ]]; then
    step "prettier" bash -c "cd web && npx prettier --write --log-level warn ."
    step "tsc" bash -c "cd web && npm run --silent typecheck"
    step "oxlint" bash -c "cd web && npm run --silent lint"
    step "vitest" bash -c "cd web && npx vitest run --reporter=dot 2>&1 | tail -15; exit \${PIPESTATUS[0]}"
fi

if ((${#failed[@]})); then
    printf '\nFailed: %s\n' "${failed[*]}"
    exit 1
fi
printf '\nAll checks passed.\n'
