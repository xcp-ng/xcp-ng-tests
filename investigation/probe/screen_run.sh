#!/usr/bin/env bash
# Probe-only minimal screen launcher (see investigation/PLAN.md, phase 1).
# Unlike detash.sh: no "continue" injection, no screen quit on TERM.
# It just logs the signals it gets, and exits on TERM.
#
# Usage: screen_run.sh <session name> <cmd...>
name=$1; shift
dir="${WORKSPACE:-$PWD}/.jenkins-pytest"
mkdir -p "$dir"
log="$dir/screen.log"
probe="$dir/probe.log"
: > "$log"

plog() { echo "$(date +%H:%M:%S.%N) [launcher $$] $*" >> "$probe"; }
for sig in TERM HUP INT; do
    trap "plog 'got SIG$sig'; [ $sig = TERM ] && { kill \$tailpid 2>/dev/null; exit 143; }" $sig
done

plog "start: screen -dmS $name teetty --out $log -- $*"
screen -dmS "$name" teetty --out "$log" -- "$@"
echo "Attach with: ssh -t $USER@$(hostname -f) screen -r $name"
tail -n +1 -F "$log" &
tailpid=$!
while screen -ls "$name" > /dev/null 2>&1; do
    sleep 1
done
plog "screen session ended"
sleep 1
kill $tailpid 2>/dev/null
