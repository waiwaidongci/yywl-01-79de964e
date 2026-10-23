#!/bin/bash

set -euo pipefail
umask 022

PYTEST_VERSION=9.1.1
WATCHDOG_SEC=90
PREFLIGHT_TEST=/tests/test_verifier_contract.py

LOG_DIR=/logs/verifier
TEST_DIR=/tests
REWARD_FILE="$LOG_DIR/reward.txt"
STATUS_FILE="$LOG_DIR/verification-status.txt"
TEST_LOG="$LOG_DIR/test-stdout.txt"
PREFLIGHT_LOG="$LOG_DIR/preflight-stdout.txt"
REPORT="$LOG_DIR/pytest-results.xml"
DEPS_DIR="$LOG_DIR/verifier-deps"
SCORE_SCRIPT=/tests/score.py
SCORING_CONFIG=/tests/scoring.json
PYTHON=/usr/local/bin/python3
TIMEOUT=/usr/bin/timeout
ENV=/usr/bin/env
completed=0

write_reward() {
  local value="$1"
  /bin/chmod 0644 "$REWARD_FILE" 2>/dev/null || true
  printf '%s\n' "$value" > "$REWARD_FILE" 2>/dev/null || true
  /bin/chmod 0444 "$REWARD_FILE" 2>/dev/null || true
}

write_status() {
  /bin/chmod 0644 "$STATUS_FILE" 2>/dev/null || true
  printf '%s\n' "$1" > "$STATUS_FILE" 2>/dev/null || true
  /bin/chmod 0444 "$STATUS_FILE" 2>/dev/null || true
}

finish_incomplete() {
  write_status "$1"
  write_reward 0.000
  completed=1
  exit 0
}

on_signal() {
  write_status "INCOMPLETE: verifier interrupted before trusted pytest completion"
  write_reward 0.000
  completed=1
  exit 0
}

on_exit() {
  local code=$?
  if [ "$completed" -ne 1 ]; then
    write_status "INCOMPLETE: verifier exited before trusted pytest completion"
    write_reward 0.000
  fi
  exit "$code"
}

trap on_signal INT TERM HUP
trap on_exit EXIT

/bin/mkdir -p "$LOG_DIR" 2>/dev/null || true
write_reward 0.000
write_status "INCOMPLETE: verifier initialization started"

if [ ! -x "$PYTHON" ] || [ ! -x "$TIMEOUT" ] || [ ! -x "$ENV" ]; then
  finish_incomplete "INCOMPLETE: trusted verifier executables are unavailable"
fi

/bin/rm -rf "$DEPS_DIR"
/bin/mkdir -p "$DEPS_DIR"
if ! "$ENV" -i \
  PATH=/usr/local/bin:/usr/bin:/bin \
  HOME=/tmp \
  LANG=C.UTF-8 \
  PYTHONNOUSERSITE=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PYTHON" -m pip install \
    --disable-pip-version-check \
    --no-cache-dir \
    --no-index \
    --find-links "$TEST_DIR/wheels" \
    --target "$DEPS_DIR" \
    "pytest==$PYTEST_VERSION" > "$LOG_DIR/pytest-install.txt" 2>&1; then
  finish_incomplete "INCOMPLETE: isolated pytest runtime could not be prepared"
fi

if [ -n "$PREFLIGHT_TEST" ]; then
  "$ENV" -i \
    PATH=/usr/local/bin:/usr/bin:/bin \
    HOME=/tmp \
    LANG=C.UTF-8 \
    PYTHONNOUSERSITE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PYTHONPATH="$DEPS_DIR:$TEST_DIR" \
    "$TIMEOUT" --signal=TERM --kill-after=5s 20s \
    "$PYTHON" -S -s -P -B -m pytest \
      -p no:cacheprovider \
      "$PREFLIGHT_TEST" > "$PREFLIGHT_LOG" 2>&1
  PREFLIGHT_RC=$?
  /bin/cat "$PREFLIGHT_LOG" 2>/dev/null || true
  if [ "$PREFLIGHT_RC" -ne 0 ]; then
    finish_incomplete "INCOMPLETE: verifier contract preflight failed"
  fi
fi

/bin/rm -f "$REPORT"
: > "$TEST_LOG"

set +e
"$ENV" -i \
  PATH=/usr/local/bin:/usr/bin:/bin \
  HOME=/tmp \
  LANG=C.UTF-8 \
  PYTHONNOUSERSITE=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PYTHONPATH="$DEPS_DIR:$TEST_DIR" \
  "$TIMEOUT" --signal=TERM --kill-after=5s "${WATCHDOG_SEC}s" \
  "$PYTHON" -S -s -P -B -m pytest \
    -p no:cacheprovider \
    -rA \
    --junitxml="$REPORT" \
    "$TEST_DIR/test_outputs.py" > "$TEST_LOG" 2>&1
RC=$?
set -e

/bin/cat "$TEST_LOG" 2>/dev/null || true

if [ "$RC" -ne 0 ] && [ "$RC" -ne 1 ]; then
  finish_incomplete "INCOMPLETE: trusted pytest did not complete with code $RC"
fi
if [ ! -s "$REPORT" ]; then
  finish_incomplete "INCOMPLETE: trusted pytest completion report is missing"
fi

if ! "$ENV" -i \
  PATH=/usr/local/bin:/usr/bin:/bin \
  HOME=/tmp \
  LANG=C.UTF-8 \
  PYTHONNOUSERSITE=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$TEST_DIR" \
  "$PYTHON" -S -s -P -B "$SCORE_SCRIPT" \
    --config "$SCORING_CONFIG" \
    --junit "$REPORT" \
    --report-out "$LOG_DIR/scoring-report.json" \
    --reward-out "$REWARD_FILE"; then
  finish_incomplete "INCOMPLETE: weighted scorer rejected the trusted completion report"
fi

REWARD="$(/bin/cat "$REWARD_FILE" 2>/dev/null || true)"
case "$REWARD" in
  0.[0-9][0-9][0-9]|1.000) ;;
  *)
    finish_incomplete "INCOMPLETE: weighted scorer produced an invalid reward"
    ;;
esac

if ! "$ENV" -i \
  PATH=/usr/local/bin:/usr/bin:/bin \
  HOME=/tmp \
  PYTHONNOUSERSITE=1 \
  PYTHONPATH="$TEST_DIR" \
  "$PYTHON" -S -s -P -B - "$LOG_DIR/scoring-report.json" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
if payload.get("status") != "COMPLETED":
    raise SystemExit(1)
if not isinstance(payload.get("reward"), (int, float)):
    raise SystemExit(1)
PY
then
  finish_incomplete "INCOMPLETE: scoring completion report is invalid"
fi

/bin/chmod 0444 "$REWARD_FILE"
write_status "COMPLETED: trusted weighted scoring finished with reward=$REWARD"
completed=1
exit 0
