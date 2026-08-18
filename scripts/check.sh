#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Emerald Rozalia - Project 1 : quality gate
#
# Run this before every commit and in CI. It is deliberately provider-agnostic
# so it works from a terminal, a git hook, or any CI runner.
#
#   ./scripts/check.sh
#
# Requires the project dependencies to be installed (pip install -r
# requirements.txt) and DJANGO_SETTINGS_MODULE to resolve to working settings.
#
# The template gate matters most. An HTML formatter once rewrote Django tag
# interiors across templates/ and took 30 of 39 templates and 91 of 95 pages
# out of service, with nothing to detect it. TemplateIntegrityTests now fails
# on both the symptom (a template that will not compile) and the cause (a tag
# split across lines). See TECHNICAL_ASSESSMENT.md 2.2.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PYTHON="${PYTHON:-python}"
failed=0

step() {
  local label="$1"; shift
  printf '\n=== %s ===\n' "$label"
  if "$@"; then
    printf 'PASS: %s\n' "$label"
  else
    printf 'FAIL: %s\n' "$label"
    failed=1
  fi
}

step "django system checks"        "$PYTHON" manage.py check
step "models match migrations"     "$PYTHON" manage.py makemigrations --check --dry-run
# core.settings_test inherits the real configuration - including the
# authorisation middleware and the security settings - and only swaps the
# password hasher and cache backend, so the suite is not dominated by PBKDF2 and
# does not wait on a Redis host that exists only inside the compose stack.
step "test suite (templates, routes, authorisation, file access, hardening)" \
     "$PYTHON" manage.py test portal --settings=core.settings_test --verbosity 1

printf '\n'
if [ "$failed" -ne 0 ]; then
  echo "check: FAILED - do not commit or deploy"
  exit 1
fi
echo "check: all gates passed"
