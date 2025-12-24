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


DETASH_NAME="${DETASH_NAME:=detash-$$}"  # session name can be overriden from env

[ ! -z "$tmpdir" ] \
    || tmpdir=$(mktemp --directory --suffix="-detash-${DETASH_NAME}.tmp.dir")
statusfile="$tmpdir/status.tmp"
logfile="${tmpdir}/screenlog.tmp"

trace=$(echo $- | grep "x" > /dev/null && echo true || echo false)


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
  screen -r jenkins-test-42 # Ctrl+a d (to deatch again)
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


on_quit_()
{
    ! $trace || set -x # Restore trace when debuging
    local reason="UNKNOWN"
    [ -z "$1" ] || reason="$1"
    local pid=$$
    local status=255
    echo "info: on_quit_: reason=$reason, pid=$$"
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

    rm -rf "${tmpdir}/"*".tmp" && rmdir "${tmpdir}"  # Comment to debug

    echo "info: Exiting: status=${status}, pid=$$"
    exit "0${status}"
}


run_()
{
    local defcol="\e[0m"
    local purplecol="\e[1;35m"

    echo -e "${purplecol}================================================================================${defcol}"
   
    cat<<EOF

info: Start cmdline: $@
info: Attempt to run in detached GNU screen named "${DETASH_NAME}" and trace
info: On hanging please cancel job $pid or troubleshoot it using:
info: sudo -u $USER screen -r ${DETASH_NAME} # If logged on $HOSTNAME
info: ssh -t $USER@$HOSTNAME $0 --attach ${DETASH_NAME} # Or remotely

EOF

    echo -e "${purplecol}================================================================================${defcol}\n"
    
    trap "on_quit_ TERM" TERM  # Default handler for liberating resources
    touch "$logfile"

    status=255  # Default error (when screen finish before cmdline)
    echo "$status" > "$statusfile"
    cmdline=$(printf '"%s" ' "$@")  # Escape quotes
    [ "" != "$cmdline" ] || cmdline="set"
    cmdline="status=0 ; $cmdline 2>&1 | tee $logfile || status=\$? ; \
     echo \${status} > $statusfile ; \
     sleep 1"  # Log cmdline output unbuffered and forward status to quit_

    screen -d -m \
           -S "${DETASH_NAME}" \
           -- bash -x -o pipefail -c "$cmdline"  # Run in bg, blocked by tail

    sleep 1
    { tail -f "$logfile" || echo "info: log no more displayed [$?] $logfile" ; } &

    set +x  # Hide trace to prevent noise in tail
    while true ; do
        sync
        sleep 1 ||: # "Wait and cooperative exit" (WCE)
        screen -ls "${DETASH_NAME}" > /dev/null 2>&1 || break;  # C-a \ shortcut
    done

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
            usage_ "$@"
            list_
            [ -z "$1" ] || run_ "$@"
            ;;
    esac
}


main_ "$@"
