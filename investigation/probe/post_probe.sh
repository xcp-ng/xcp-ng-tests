#!/usr/bin/env bash
# Probe-only post/always step (see investigation/PLAN.md, phase 1).
# Prints what the probe recorded, follows it while pytest is alive,
# then cleans up: SIGINT, 3s, SIGKILL.
dir="${WORKSPACE:-$PWD}/.jenkins-pytest"
probe="$dir/probe.log"
pid=$(cat "$dir/pytest.pid" 2>/dev/null)
max=${PROBE_MAX_WAIT:-240}

plog() { echo "$(date +%H:%M:%S.%N) [post $$] $*" >> "$probe"; }
trap 'plog "got SIGTERM (second abort)"; [ -n "$pid" ] && kill -INT "$pid"; sleep 3; kill -KILL "$pid" 2>/dev/null; exit 143' TERM

plog "post start; pytest pid=$pid alive=$(kill -0 "$pid" 2>/dev/null && echo yes || echo no)"
echo "--- process tree at post start ---"
ps -e --forest -o pid,ppid,pgid,sid,tty,stat,cmd | grep -E -A3 "pytest|screen|teetty" | grep -v grep
screen -ls || true
echo "--- probe.log so far ---"
cat "$probe"
echo "--- following probe.log while pytest is alive (max ${max}s) ---"
tail -n 0 -F "$probe" &
tailpid=$!
start=$SECONDS
while [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && [ $((SECONDS - start)) -lt "$max" ]; do
    sleep 1
done
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    plog "pytest still alive after ${max}s: SIGINT"
    kill -INT "$pid"; sleep 3
    kill -0 "$pid" 2>/dev/null && { plog "still alive: SIGKILL"; kill -KILL "$pid"; }
fi
plog "post end; pytest alive=$(kill -0 "$pid" 2>/dev/null && echo yes || echo no)"
sleep 1
kill $tailpid 2>/dev/null
echo "--- pytest.log (output after the abort) ---"
cat "$dir/pytest.log" 2>/dev/null || echo "(none)"
echo "--- status: $(cat "$dir/status" 2>/dev/null || echo none) ---"
echo "--- screen.log tail ---"
tail -n 30 "$dir/screen.log" 2>/dev/null
screen -ls || true
