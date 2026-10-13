#!/usr/bin/env bash
# Local simulation of an aborted Jenkins job, with the signal waves seen in FINDINGS.md runs 1-4.
#
# Usage: simulate_jenkins.sh <debug: 0|1> <abort at: seconds|none> <second abort: seconds after post start|none>
# (the timeout of the post step is a second abort)
#
# - step: [run NAME] pytest on investigation/experiments/test_local_debug.py, writing to a jenkins-log.txt in
#   <workspace>@tmp (opened with ">", as by Jenkins)
# - first abort: wave 1 = SIGTERM to the step processes (pytest, launcher, its tail); wave 2 (+5s) = SIGTERM to
#   pytest again, and in DEBUG mode screen/teetty killed (SIGHUP to pytest). Jenkins displays the step's output
#   until wave 2, then deletes jenkins-log.txt
# - post (+11s after the abort, or when the step ends): scripts/jenkins_pytest.py monitor
# - second abort: wave 1 = SIGTERM to pytest; wave 2 (+6s) = SIGTERM to the monitor
debug=$1; abort_at=$2; second=$3
here=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$here/../.." && pwd)
export WORKSPACE=$(mktemp -d /tmp/simjenkins-XXXX) JENKINS_PYTEST_ABORT=1 BUILD_TAG="sim job-1" BUILD_NUMBER=1
export WORKSPACE_TMP="$WORKSPACE@tmp"
export CALL_S=${CALL_S:-20} TEARDOWN_S=${TEARDOWN_S:-15}
state=$WORKSPACE_TMP/pytest-$BUILD_NUMBER
dirty=$WORKSPACE/.jenkins-pytest/dirty  # see test_local_debug.py
cd "$repo"
export PATH="$repo/.venv/bin:$PATH"  # as test_env.groovy does on Jenkins
t0=$SECONDS
note() { echo "### [sim t+$((SECONDS - t0))s] $*"; }

# Background jobs of this script start with SIGINT ignored: restore it, as in a real Jenkins step
cmd=(.venv/bin/python -c "import os,signal,sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execv(sys.argv[1], sys.argv[1:])"
     .venv/bin/pytest "${TESTS:-investigation/experiments/test_local_debug.py}" -p no:cacheprovider ${PYTEST_EXTRA:-})
if [ "$debug" = 1 ]; then
    cmd=(scripts/jenkins_pytest.py run sim-$$ "${cmd[@]}" --pdb)
fi
ctl=$WORKSPACE_TMP/durable-sim
mkdir -p "$ctl"
"${cmd[@]}" > "$ctl/jenkins-log.txt" 2>&1 &
step=$!
note "step started: ${cmd[*]}"

if [ "$abort_at" != none ]; then
    while [ $((SECONDS - t0)) -lt "$abort_at" ]; do sleep 0.2; done
    pytest=$(cat "$state/pytest.pid")
    note "ABORT wave 1: SIGTERM to pytest $pytest, step $step and its tail"
    kill -TERM "$pytest" "$step" $(pgrep -P "$step" -x tail) 2>/dev/null
    sleep 5
    note "ABORT wave 2: SIGTERM to pytest, screen/teetty killed, step log no more displayed, then deleted"
    head -c "$(stat -c %s "$ctl/jenkins-log.txt")" "$ctl/jenkins-log.txt" > "$WORKSPACE/displayed.log"
    if [ "$debug" = 1 ]; then
        screen -S sim-$$ -X quit 2>/dev/null
    fi
    kill -TERM "$pytest" 2>/dev/null
    rm -rf "$ctl"
    sleep 6
else
    wait "$step"
    cp "$ctl/jenkins-log.txt" "$WORKSPACE/displayed.log"
fi
note "step exit: $(wait "$step"; echo $?)"

note "POST: monitor starts"
scripts/jenkins_pytest.py monitor > "$WORKSPACE/post.log" 2>&1 &
mon=$!
if [ "$second" != none ]; then
    sleep "$second"
    note "SECOND ABORT wave 1: SIGTERM to pytest"
    kill -TERM "$(cat "$state/pytest.pid")" 2>/dev/null
    sleep 6
    note "SECOND ABORT wave 2: SIGTERM to the monitor"
    kill -TERM "$mon" 2>/dev/null
fi
wait "$mon"; note "monitor exit: $?"

pytest=$(cat "$state/pytest.pid")
echo "=== RESULT: pytest alive=$(kill -0 "$pytest" 2>/dev/null && echo YES || echo no)" \
     "dirty=$([ -e "$dirty" ] && echo LEFT || echo removed)" \
     "monitor.pid=$([ -e "$state/monitor.pid" ] && echo LEFT || echo removed)"
echo "=== pytest summary: $(cat "$WORKSPACE/displayed.log" "$WORKSPACE/post.log" | grep -aE '=+ .* in [0-9.]+s' | tail -n 1)"
echo "=== abort file: $(cat "$state/abort" 2>/dev/null || echo none); displayed by the step: $(stat -c %s "$WORKSPACE/displayed.log") bytes"
clean() { sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g; s/\r//g'; }
echo "=== step console (tail):"; tail -n 5 "$WORKSPACE/displayed.log" | clean
echo "=== post console (head):"; head -n 5 "$WORKSPACE/post.log" | clean
echo "=== post console (tail):"; tail -n 4 "$WORKSPACE/post.log" | clean
kill -KILL "$pytest" 2>/dev/null; screen -S sim-$$ -X quit > /dev/null 2>&1
[ -n "$KEEP" ] && echo "kept: $WORKSPACE" || rm -rf "$WORKSPACE" "$WORKSPACE_TMP"
