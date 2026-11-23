#!/bin/sh
# ---------------------------------------------------------------------------
# mock-scenarios.sh -- run all offline mock scenarios and verify final state.
#
# Run from the patcher root in a container with Python 3:
#   docker run --rm -v "<patcher>:/work" -w /work mazda-tools \
#       sh test/mock-scenarios.sh
# ---------------------------------------------------------------------------
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd) || exit 1
MOCK="$ROOT/mock_hu_patcher.py"
OUT=$(mktemp) || exit 1
trap 'rm -f "$OUT"' EXIT INT TERM HUP

fail=0
check() {
    desc=$1
    shift
    if "$@"; then
        echo "ok   - $desc"
    else
        echo "FAIL - $desc"
        fail=1
    fi
}

run_scenario() {
    scen=$1
    echo
    echo "== $scen =="
    if python3 "$MOCK" --scenario "$scen" >"$OUT" 2>&1; then
        echo "ok   - exit 0"
    else
        echo "FAIL - exit 0"
        cat "$OUT"
        fail=1
    fi
    if grep -q 'no scripted answer' "$OUT"; then
        echo "FAIL - all prompts scripted"
        fail=1
    else
        echo "ok   - all prompts scripted"
    fi
    cat "$OUT"
}

check_calls() {
    desc=$1
    max=$2
    calls=$(sed -n 's/.*ssh calls: \([0-9][0-9]*\).*/\1/p' "$OUT" | tail -n 1)
    if [ -n "$calls" ] && [ "$calls" -le "$max" ]; then
        echo "ok   - $desc: $calls SSH calls (<= $max)"
    else
        echo "FAIL - $desc: ${calls:-no} SSH calls (> $max)"
        fail=1
    fi
}

check_first_apply() {
    desc=$1
    want=$2
    first=$(grep -m1 '^== [A-Z]) ' "$OUT" 2>/dev/null || true)
    case "$first" in
        *"$want"*) echo "ok   - $desc: $first" ;;
        *) echo "FAIL - $desc: first apply '${first:-none}'"; fail=1 ;;
    esac
}

check_last_apply() {
    desc=$1
    want=$2
    last=$(grep '^== [A-Z]) ' "$OUT" 2>/dev/null | tail -n 1 || true)
    case "$last" in
        *"$want"*) echo "ok   - $desc: $last" ;;
        *) echo "FAIL - $desc: last apply '${last:-none}'"; fail=1 ;;
    esac
}

run_scenario no-root
check "no-root: root obtained" grep -q 'root      : yes' "$OUT"
check "no-root: A installed" grep -q 'A (GPS)   : patched' "$OUT"
check "no-root: B installed" grep -q 'B (touch) : patched' "$OUT"
check "no-root: C installed" grep -Eq '^  C +: patched' "$OUT"
check "no-root: D installed" grep -Eq '^  D +: patched' "$OUT"
check_first_apply "no-root: Fast Login first" "D) Fast Login"
check "no-root: timezone +3" grep -q 'Etc/GMT-3' "$OUT"
check_calls "no-root" 45

run_scenario root-one
check "root-one: root present" grep -q 'root      : yes' "$OUT"
check "root-one: A rolled back" grep -q 'A (GPS)   : original' "$OUT"
check "root-one: B installed" grep -q 'B (touch) : patched' "$OUT"
check "root-one: C installed" grep -Eq '^  C +: patched' "$OUT"
check "root-one: D installed" grep -Eq '^  D +: patched' "$OUT"
check_first_apply "root-one: Fast Login first" "D) Fast Login"
check "root-one: timezone restored" grep -q 'timezone  : Asia/Shanghai' "$OUT"
check_calls "root-one" 30

run_scenario conflict
check "conflict: root obtained" grep -q 'root      : yes' "$OUT"
check "conflict: A installed" grep -q 'A (GPS)   : patched' "$OUT"
check "conflict: B conflict" grep -q 'B (touch) : unknown' "$OUT"
check "conflict: C installed" grep -Eq '^  C +: patched' "$OUT"
check "conflict: D installed" grep -Eq '^  D +: patched' "$OUT"
check "conflict: conflict message" grep -q 'Skipping: unknown file version' "$OUT"
check_calls "conflict" 42

run_scenario manual-reboot
check "manual-reboot: root obtained" grep -q 'root      : yes' "$OUT"
check "manual-reboot: A installed" grep -q 'A (GPS)   : patched' "$OUT"
check "manual-reboot: B installed" grep -q 'B (touch) : patched' "$OUT"
check "manual-reboot: C installed" grep -Eq '^  C +: patched' "$OUT"
check "manual-reboot: D installed" grep -Eq '^  D +: patched' "$OUT"
check "manual-reboot: manual instruction" grep -q 'hold Music, Favorite' "$OUT"
check "manual-reboot: diag mode" grep -q "End Diag Mode" "$OUT"
check_calls "manual-reboot" 45

run_scenario outdated
check "outdated: status" grep -q 'Outdated' "$OUT"
check "outdated: version" grep -q '0.9.0 -> 1.0.0' "$OUT"
check "outdated: updated" grep -q 'C: updated' "$OUT"
check "outdated: C installed" grep -Eq '^  C +: patched' "$OUT"
check_calls "outdated" 30

run_scenario newer
check "newer: version" grep -q '9.9.9' "$OUT"
check "newer: conflict" grep -q 'Conflict (unknown file version)' "$OUT"
check "newer: no changes" grep -q 'No patch changes selected' "$OUT"
check_calls "newer" 5

run_scenario rollback-all
check "rollback-all: A removed" grep -q 'A (GPS)   : original' "$OUT"
check "rollback-all: D removed" grep -q 'D         : original' "$OUT"
check_first_apply "rollback-all: normal first" "A) GPS/timezone"
check_last_apply "rollback-all: Fast Login last" "D) Fast Login"
check_calls "rollback-all" 40

echo
if [ "$fail" -eq 0 ]; then
    echo "MOCK_SCENARIOS: OK"
    exit 0
fi
echo "MOCK_SCENARIOS: FAILED"
exit 1
