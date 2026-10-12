#! /usr/bin/env bash
# -*- mode: Bash; tab-width: 2; indent-tabs-mode: nil; coding: utf-8 -*-
# vim:shiftwidth=4:softtabstop=4:tabstop=4:
#
# SPDX-FileCopyrightText: Philippe Coval <philippe.coval@vates.tech>
#
# SPDX-License-Identifier: MIT
#

# set -x  # Trace for debugging, TODO: Comment when stable
set -e  # Fail on error
set -o pipefail
set -m  # Job control


SELF=$(realpath -- "$0")
DETASH_NAME="${DETASH_NAME:=detash-$$}"  # session name can be overriden from env

[ ! -z "$tmpdir" ] \
    || tmpdir=$(mktemp --directory --suffix="-detash-${DETASH_NAME}.tmp.dir")
statusfile="$tmpdir/status.tmp"
logfile="${tmpdir}/screenlog.tmp"

trace=$(echo $- | grep "x" > /dev/null && echo true || echo false)

# Debug trace: stderr reaches the job log directly, while screen content reaches
# it a second time via the `tail -f` below. Keeping it on stderr means a
# DETASHDBG line seen once came from here, seen twice came from the screen.
# Written outside $tmpdir, which on_quit_ removes before the script exits.
dbgfile="${TMPDIR:-/tmp}/detash-debug.$$.log"
dbg_()
{
    local line
    line="$(date +%H:%M:%S.%3N) DETASHDBG[$$] $*"
    printf '%s\n' "${line}" >&2
    printf '%s\n' "${line}" >> "${dbgfile}" 2>/dev/null ||:
}
dbg_ "start $0 args: $*"
dbg_ "tmpdir=${tmpdir} logfile=${logfile} dbgfile=${dbgfile} DETASH_NAME=${DETASH_NAME}"
dbg_ "ancestry: $(ps -o pid=,ppid=,comm= -p $$ --ppid $$ --forest 2>/dev/null | tr -s ' ' | tr '\n' '|')"


usage_()
{
    cat<<EOF

Usage: detash.sh <command> [args]
Run command in DETAched SHell using GNU screen

Command / args:

  -h, --help : Display usage
  -l, --list : List detached sessions
  -a, --attach [name] : Attach named session (or latest) [${DETASH_NAME}]
  -r, --run <cmdline> : Run cmdline in detached mode (same for -- <cmdline>)
  <cmdline> : Print help, list sessions and run cmdline

About:

  This script runs a command line in a detached GNU screen
  then allows to attach to the session from an other shell.
  It display the output to stdout and forward the status code.

  It can be used in CI environment (eg: jenkins) for troubleshooting.

Attaching running screen session:

  screen -ls
  screen -r jenkins-test-42 # Ctrl+a d (to detach again)
  ssh -t $USER@$HOSTNAME screen -r

Notes:

  Warning: This hackish solution may raise security concerns
  (non determistic run, reentrance, DOS, injections...)

Environment:

  DETASH_NAME : GNU Screen's session name [${DETASH_NAME}]
  DETASH_ON_QUIT : Hook on termination (eg: "continue" for debugger) [${DETASH_ON_QUIT}]

Examples:

  ./detash.sh sleep 42 # Exit success once over
  DETASH_NAME=find ./detash.sh find / # May fail on perms and exit status
  DETASH_ON_QUIT=h ./detash.sh top ; sleep 1 ; pkill -f detash.sh # Display help on quit
  DETASH_NAME=detash-${BUILD_TAG} ./detash.sh pytest # Name session matching jenkins env
  DETASH_ON_QUIT=continue ./detash.sh pytest --pdb ; # Teardown pytest on term of script
EOF
}


list_()
{
    echo "info: Available sessions on host=$HOSTNAME for user=$USER:"
    screen -ls "$@" ||:
}


on_term_()  # Forward SIGTERM to the cmdline running in the screen session
{
    ! $trace || set -x # Restore trace when debuging
    local reason="${1:-TERM}"
    local pid screen_pid=""
    dbg_ "on_term_ enter reason=${reason}"
    dbg_ "on_term_ pgrep -f -- -S '${DETASH_NAME}' => [$(pgrep -af -- "-S ${DETASH_NAME}" 2>&1 | tr '\n' '|')]"
    for pid in $(pgrep -f -- "-S ${DETASH_NAME}" ||:) ; do
        [ "$$" != "$pid" ] || continue  # Never signal ourselves
        case "$(ps -o comm= -p "$pid" ||:)" in
            SCREEN | screen) screen_pid="$pid" ; break ;;
        esac
    done
    echo "info: on_term_: reason=${reason}, screen_pid=${screen_pid:-none}"
    dbg_ "on_term_ resolved screen_pid=${screen_pid:-none}"
    if [ ! -z "$screen_pid" ]; then
        dbg_ "on_term_ children of screen_pid: [$(pgrep -P "$screen_pid" 2>&1 | tr '\n' '|')]"
        pkill -TERM -P "$screen_pid" ||:  # Forward TERM, screen left alive
        dbg_ "on_term_ pkill -TERM -P ${screen_pid} done, return"
    else
        dbg_ "on_term_ nothing to forward to, return"
    fi
    return 0
}


on_quit_()
{
    ! $trace || set -x # Restore trace when debuging
    local reason="UNKNOWN"
    [ -z "$1" ] || reason="$1"
    local pid=$$
    local status=255
    echo "info: on_quit_: reason=$reason, pid=$$"
    dbg_ "on_quit_ enter reason=${reason} pid=${pid} statusfile=$(cat "$statusfile" 2>/dev/null || echo unreadable)"
    dbg_ "on_quit_ screen -ls => [$(screen -ls 2>&1 | tr '\n' '|')]"
    dbg_ "on_quit_ DETASH_ON_QUIT=${DETASH_ON_QUIT:-<unset>}"
    screen -ls ||:
    echo "info: Type DETASH_ON_QUIT=${DETASH_ON_QUIT} if defined before quitting screen"
    if [ ! -z "${DETASH_ON_QUIT}" ] ; then
        screen -ls "${DETASH_NAME}" \
            && screen -S "${DETASH_NAME}" \
                      -X stuff "${DETASH_ON_QUIT}^M" \
                ||:
        screen -ls "${DETASH_NAME}" \
            && screen -S "${DETASH_NAME}" \
                      -X sleep 1 \
                ||:
        sleep 1 ; sync
    fi
    screen -ls "${DETASH_NAME}" >/dev/null 2>&1 \
        && screen -S "${DETASH_NAME}" -X "quit" ||:  # Safer quit
    sleep 1 ; sync # Flush logs
    tail -n 24 "${logfile}"  # To show last page of log
    status=$(<"$statusfile")

    jobs=$(jobs -p ||:)  # Subshell running in background
    [ -z "$jobs" ] || jobs=$(pgrep -P "$jobs" ||:)  # Processes of subshell
    [ -z "$jobs" ] || kill "${jobs}"  # Terminate procs if any (tail)
    dbg_ "on_quit_ killed background jobs: [${jobs:-none}]"

    rm -rf "${tmpdir}/"*".tmp" && rmdir "${tmpdir}"  # Comment to debug

    echo "info: Exiting: status=${status}, pid=$$"
    dbg_ "on_quit_ exit 0${status} (exit code will be $((0${status})))"
    exit "0${status}"
}


run_()
{
    local defcol="\e[0m"
    local purplecol="\e[1;35m"
    local domain=".$(domainname)"
    [ "$domain" = ".(none)" ] && domain=""

    echo -e "${purplecol}================================================================================${defcol}"

    cat<<EOF

info: Start cmdline: $@
info: Attempt to run in detached GNU screen named "${DETASH_NAME}" and trace
info: On hanging please cancel job $pid or troubleshoot it using:
info: screen -r "${DETASH_NAME}" # If logged on "$HOSTNAME$domain"
info: ssh -t "$USER@$HOSTNAME$domain" screen -rx \"${DETASH_NAME}\" # One liner
info: ssh -t "$USER@$HOSTNAME$domain" \"$SELF\" --attach \"${DETASH_NAME}\" # Or using wrapper

info: If hostname "${HOSTNAME}$domain" is not resolvable then try to ssh to the following IPs:

$( ip a \
  | sed -n -e 's|.*inet[6]* \([0-9a-f\:.]*\)/.*|\1|p' \
  | sed -e '/^127\.0\.0\.1$/d' -e '/^::1$/d')

EOF

    echo -e "${purplecol}================================================================================${defcol}\n"

    dbg_ "run_ arming trap: GOT-TERM is logged here before delegating to on_term_"
    trap 'dbg_ "GOT-TERM (trap fired, deferring to on_term_)"; on_term_' TERM  # Forward SIGTERM to cmdline, run_ loop will quit
    touch "$logfile"

    status=255  # Default error (when screen finish before cmdline)
    echo "$status" > "$statusfile"
    cmdline=$(printf '"%s" ' "$@")  # Escape quotes
    [ "" != "$cmdline" ] || cmdline="set"
    cmdline="status=0 ; teetty --out \"$logfile\" -- $cmdline || status=\$? ; \
     echo \${status} > \"$statusfile\" ; \
     sleep 1"  # Log cmdline output unbuffered and forward status to quit_

    screen -d -m \
           -S "${DETASH_NAME}" \
           -- bash -x -o pipefail -c "$cmdline"  # Run in bg, blocked by tail
    dbg_ "run_ screen -d -m launched, name='${DETASH_NAME}'"

    sleep 1
    { tail -f "$logfile" || echo "info: log no more displayed [$?] $logfile" ; } &

    set +x  # Hide trace to prevent noise in tail
    local prev_rc=0 iter=0 t0 t1
    while true ; do
        # bash only runs traps *between* commands, so a signal arriving while we
        # sit in one of these is deferred until it returns. Time them and report
        # anything slow, since that is where a deferred trap goes to hide.
        t0=$(date +%s%3N); sync; t1=$(date +%s%3N)
        [ $((t1-t0)) -lt 500 ] || dbg_ "run_ SLOW sync took $((t1-t0))ms (trap would be deferred here)"
        sleep 1 ||: # "Wait and cooperative exit" (WCE)
        t0=$(date +%s%3N)
        screen -ls "${DETASH_NAME}" > /dev/null 2>&1; rc=$?
        t1=$(date +%s%3N)
        [ $((t1-t0)) -lt 500 ] || dbg_ "run_ SLOW screen -ls took $((t1-t0))ms (trap would be deferred here)"
        [ "$rc" == "$prev_rc" ] || dbg_ "run_ screen -ls rc changed ${prev_rc} -> ${rc}"
        prev_rc=$rc
        iter=$((iter+1))
        [ $((iter % 30)) != 0 ] || dbg_ "run_ heartbeat iter=${iter} screen -ls rc=${rc}"
        [ "$rc" == 0 ] && continue
        # Session is gone: capture exactly what we can still see about who did it.
        dbg_ "run_ session gone at iter=${iter}; screen -ls => [$(screen -ls 2>&1 | tr '\n' '|')]"
        dbg_ "run_ pgrep -af -- -S '${DETASH_NAME}' => [$(pgrep -af -- "-S ${DETASH_NAME}" 2>&1 | tr '\n' '|')]"
        dbg_ "run_ statusfile => [$(cat "$statusfile" 2>/dev/null || echo unreadable)]"
        break;  # C-a \ shortcut
    done

    dbg_ "run_ loop exited after ${iter} iterations"
    on_quit_ "OVER"
}


main_()
{
    case $1 in
        --help | -h)
            shift
            usage_ "$@"
            ;;
        --run | -r | --)
            shift
            run_ "$@"
            ;;
        --list | -l)
            shift
            list_ "$@"
            ;;
        --attach | -a)
            shift
            screen -r "$1" || list_
            ;;
        *)
            [ -z "$1" ] || run_ "$@"
            ;;
    esac
}


main_ "$@"
