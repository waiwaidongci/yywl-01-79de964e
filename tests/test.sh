#!/bin/bash
set -euo pipefail

LOG_DIR=/logs/verifier
REWARD_FILE=/logs/verifier/reward.txt
STATUS_FILE="$LOG_DIR/verification-status.txt"
TEST_LOG="$LOG_DIR/test-stdout.txt"
REPORT="$LOG_DIR/pytest-results.xml"
completed=0

mkdir -p "$LOG_DIR"

write_reward() {
  local value="$1"
  chmod 0644 "$REWARD_FILE" 2>/dev/null || true
  printf '%s\n' "$value" > /logs/verifier/reward.txt
  chmod 0444 "$REWARD_FILE"
}

write_status() {
  chmod 0644 "$STATUS_FILE" 2>/dev/null || true
  printf '%s\n' "$1" > "$STATUS_FILE"
  chmod 0444 "$STATUS_FILE"
}

on_exit() {
  if [ "$completed" -ne 1 ]; then
    write_status "INCOMPLETE: verifier exited before weighted scoring finished"
    write_reward 0.000
  fi
}

on_signal() {
  write_status "INCOMPLETE: verifier interrupted before weighted scoring finished"
  write_reward 0.000
  completed=1
  exit 0
}

write_reward 0.000
write_status "INCOMPLETE: verifier has not started pytest"
trap on_exit EXIT
trap on_signal INT TERM HUP

unset PYTEST_ADDOPTS PYTEST_PLUGINS
export PYTHONDONTWRITEBYTECODE=1

VENV=/tmp/yel004-verifier-venv
rm -rf "$VENV"
set +e
python3 -m venv "$VENV" >> "$TEST_LOG" 2>&1
VENV_RC=$?
if [ "$VENV_RC" -eq 0 ]; then
  "$VENV/bin/python" -m pip install \
    --no-index \
    --find-links /tests/wheels \
    pytest==9.1.1 >> "$TEST_LOG" 2>&1
  VENV_RC=$?
fi
set -e
if [ "$VENV_RC" -ne 0 ]; then
  write_status "INCOMPLETE: isolated pytest runtime could not be prepared"
  write_reward 0.000
  completed=1
  exit 0
fi

set +e
timeout --signal=TERM --kill-after=5s 20s \
  "$VENV/bin/python" -u -m pytest -p no:cacheprovider -q \
  /tests/test_verifier_contract.py > "$TEST_LOG" 2>&1
PREFLIGHT_RC=$?
set -e
cat "$TEST_LOG" 2>/dev/null || true
if [ "$PREFLIGHT_RC" -ne 0 ]; then
  write_status "INCOMPLETE: verifier contract preflight failed"
  write_reward 0.000
  completed=1
  exit 0
fi

rm -f "$REPORT"
set +e
timeout --signal=TERM --kill-after=5s 90s \
  "$VENV/bin/python" -u -m pytest -p no:cacheprovider -vv -s --disable-warnings \
  --junitxml="$REPORT" /tests/test_outputs.py >> "$TEST_LOG" 2>&1
RC=$?
set -e
cat "$TEST_LOG" 2>/dev/null || true

if [ ! -s "$REPORT" ]; then
  write_status "INCOMPLETE: pytest completion report missing"
  write_reward 0.000
  completed=1
  exit 0
fi

if [ "$RC" -ne 0 ] && [ "$RC" -ne 1 ]; then
  write_status "INCOMPLETE: pytest runner exited with code $RC"
  write_reward 0.000
  completed=1
  exit 0
fi

if python3 /tests/score.py \
  --config /tests/scoring.json \
  --junit "$REPORT" \
  --report-out "$LOG_DIR/scoring-report.json" \
  --reward-out "$REWARD_FILE"; then
  REWARD=$(cat "$REWARD_FILE")
  case "$REWARD" in
    0.[0-9][0-9][0-9]|1.000) ;;
    *)
      write_status "INCOMPLETE: weighted scorer produced invalid reward"
      write_reward 0.000
      completed=1
      exit 0
      ;;
  esac
  chmod 0444 "$REWARD_FILE"
  write_status "COMPLETED: weighted scoring finished with reward=$REWARD"
else
  write_status "INCOMPLETE: weighted scorer rejected the completion report"
  write_reward 0.000
fi

completed=1
exit 0
