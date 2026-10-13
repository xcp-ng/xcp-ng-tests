#!/usr/bin/env bash
# Experiment harness (PLAN.md phase 2.3): run the debug test with --pdb under
# screen + teetty, send SIGTERM once pdb is open, and report what happened.
# Usage: run_pdb_exit.sh <method> [none|kill-tty|tty-first]
#   kill-tty: quit screen 0.5s after the SIGTERM; tty-first: quit screen 0.5s before it
method=$1; after=${2:-none}
here=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$here/../.." && pwd)
export WORKSPACE=$(mktemp -d /tmp/pdbexit-$method-XXXX)
export EXP_LOG=$WORKSPACE/exp.log PDB_EXIT=$method CALL_S=1 TEARDOWN_S=3
export PYTHONPATH=$here
name=pdbexit-$$
cd "$repo"
screen -dmS "$name" teetty --out "$WORKSPACE/screen.log" -- \
    bash -c ".venv/bin/pytest investigation/experiments/test_local_debug.py -p pdb_exit -p no:cacheprovider --pdb; echo \$? > $WORKSPACE/status"
for _ in $(seq 50); do grep -q "enter pdb" "$EXP_LOG" 2>/dev/null && break; sleep 0.2; done
sleep 1
pid=$(cat "$EXP_LOG.pid")
if [ "$after" = tty-first ]; then
    screen -S "$name" -X quit; sleep 0.5
fi
if [ "$method" = type ]; then
    screen -S "$name" -X stuff "continue^M"
else
    kill -TERM "$pid"
fi
if [ "$after" = kill-tty ]; then
    sleep 0.5; screen -S "$name" -X quit
fi
for _ in $(seq 75); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
echo "=== method=$method after=$after"
cat "$EXP_LOG"
if kill -0 "$pid" 2>/dev/null; then
    echo "RESULT: pytest STILL ALIVE after 15s (hang); threads:"; ls /proc/$pid/task | wc -l
    kill -KILL "$pid"
fi
echo "RESULT: status=$(cat $WORKSPACE/status 2>/dev/null || echo none) dirty=$([ -e $WORKSPACE/.jenkins-pytest/dirty ] && echo LEFT || echo removed)"
screen -S "$name" -X quit > /dev/null 2>&1
[ -f "$EXP_LOG.out" ] && { echo "redirected output tail:"; tail -n 8 "$EXP_LOG.out" | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g'; }
echo "screen.log tail:"; tail -c 400 "$WORKSPACE/screen.log" | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' | tail -6
