#!/usr/bin/env python3

"""
Jenkins side of the clean abort of pytest (lib/jenkins.py).

When a Jenkins job running pytest is aborted, pytest tears its fixtures down after the end of the Jenkins step
(see lib/jenkins.py). This script lets the job display that teardown, and wait for it:

run NAME CMD...
    For DEBUG mode: run CMD in a detached screen session named NAME, so that one can attach to it, e.g. to use the
    debugger (pytest --pdb). Its output is copied (by teetty) to a log file, and displayed in the Jenkins console.
    Exits with the status of CMD. On abort, the output is still displayed until Jenkins kills the screen session.

monitor
    For the post section (always) of the job: after an abort, display the output of pytest from where Jenkins
    stopped displaying it, until pytest exits. Does nothing if there was no abort. While it runs, pytest takes a
    SIGTERM as a second abort (the post step is aborted, or times out), and interrupts its teardown.

Typical job, with JENKINS_PYTEST_ABORT=1 in its environment:

    stage options:  lock the test hosts (held until the end of the post section)
    steps:          [scripts/jenkins_pytest.py run NAME] ./jobs.py run ... [--pdb]
    post, always:   timeout(15 min) { scripts/jenkins_pytest.py monitor }
"""

import argparse
import getpass
import os
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

def state_dir() -> Path:
    """Directory shared with pytest, one per build: same as in lib/jenkins.py, see there."""
    tmp = os.environ.get("WORKSPACE_TMP") or f"{os.environ['WORKSPACE']}@tmp"
    return Path(tmp) / f"pytest-{os.environ['BUILD_NUMBER']}"

def tail(path: Path, pid: int, offset: int = 0) -> list[str]:
    """Command displaying the file from offset, and what is appended to it, until the process exits."""
    return ["tail", "-c", f"+{offset + 1}", "-f", f"--pid={pid}", str(path)]

def run(name: str, cmd: list[str]) -> int:
    # On abort, pytest gets the signal from Jenkins too, and goes on in the screen session: ignore it, to keep
    # displaying the output until Jenkins kills the session (a few seconds later). Child processes get the default
    # handler back.
    signal.signal(signal.SIGTERM, lambda signum, frame: None)

    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    log, status = directory / "screen.log", directory / "screen.status"
    log.write_bytes(b"")

    # teetty gives CMD a terminal of its own (needed by the debugger), and copies its output to the log
    script = 'log=$1; status=$2; shift 2; teetty --out "$log" -- "$@"; echo $? > "$status"'
    # -D -m: detached session, but screen only returns when the session ends
    screen = subprocess.Popen(["screen", "-D", "-m", "-S", name, "sh", "-c", script, "sh", str(log), str(status), *cmd])

    lines = ["DEBUG mode: to attach to pytest (and its debugger), run:",
             f"ssh -t {getpass.getuser()}@{socket.getfqdn()} screen -r {name}"]
    width = max(len(line) for line in lines)
    box = "\n".join([f"┌{'─' * (width + 2)}┐", *(f"│ {line:<{width}} │" for line in lines), f"└{'─' * (width + 2)}┘"])
    # Bold magenta, as detash.sh did
    print(f"\033[1;35m{box}\033[0m", flush=True)

    # Display the log until the screen session ends. tail --pid takes a zombie for a running process: reap screen
    # as soon as it exits. And tail must ignore SIGTERM too: an ignored signal stays ignored across exec.
    threading.Thread(target=screen.wait, daemon=True).start()
    subprocess.run(["sh", "-c", 'trap "" TERM; exec "$@"', "sh", *tail(log, screen.pid)])
    screen.wait()
    return int(status.read_text()) if status.exists() else 255

def monitor() -> None:
    directory = state_dir()
    abort = directory / "abort"
    if not abort.exists():
        return

    # The file of the output of pytest, and where Jenkins stopped displaying it
    offset, output = abort.read_text().split(" ", 1)

    # From now on, pytest takes a SIGTERM as a second abort
    (directory / "monitor.pid").write_text(f"{os.getpid()}\n")
    try:
        subprocess.run(tail(Path(output), int((directory / "pytest.pid").read_text()), int(offset)))
    finally:
        (directory / "monitor.pid").unlink(missing_ok=True)

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="action", required=True)
    run_parser = subparsers.add_parser(
        "run", help="DEBUG mode: run CMD in a detached screen session (to attach to its debugger), "
                    "displaying its output, and exit with its status")
    run_parser.add_argument("name", help="name of the screen session")
    run_parser.add_argument("cmd", nargs=argparse.REMAINDER, help="the command and its arguments")
    subparsers.add_parser("monitor", help="post step: after an abort, display the rest of the output of pytest, "
                                          "until it exits")
    args = parser.parse_args()
    if args.action == "run":
        if not args.cmd:
            run_parser.error("missing command")
        sys.exit(run(args.name, args.cmd))
    monitor()

if __name__ == "__main__":
    main()
