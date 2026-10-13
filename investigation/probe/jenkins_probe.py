"""Temporary diagnostics for Jenkins job cancellation (see investigation/PLAN.md, phase 1).

Load with: PYTHONPATH=investigation/probe pytest -p jenkins_probe ...

Everything goes to $WORKSPACE/.jenkins-pytest/probe.log, a file opened by this
process, so it keeps working whatever happens to stdout/stderr.

On the first SIGTERM/SIGHUP (the abort) it applies the planned behavior
(PLAN.md phase 3, FINDINGS.md 2.3):
- fds 0/1/2 and pytest's saved copies are redirected: stdin to /dev/null,
  stdout/stderr to $WORKSPACE/.jenkins-pytest/pytest.log
- outside a teardown: if pdb is open, it continues ("api-continue");
  otherwise KeyboardInterrupt is raised, which interrupts the test
- in a teardown: the teardown goes on
Later SIGTERM/SIGHUP are only logged. SIGINT keeps its default behavior
(KeyboardInterrupt): it is the second-abort channel.
"""

# Throwaway investigation code (removed in PLAN.md phase 7): lighter lint rules.
# ruff: noqa: ANN401, SLF001, SIM115

from __future__ import annotations

import pytest

import atexit
import logging
import os
import signal
import subprocess
import sys
import threading
import time

from typing import Any

T0 = time.monotonic()
STATE_DIR = os.path.join(os.environ.get("WORKSPACE", os.getcwd()), ".jenkins-pytest")
os.makedirs(STATE_DIR, exist_ok=True)
_log_fd = os.open(os.path.join(STATE_DIR, "probe.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
_lock = threading.Lock()

phase = "startup"
in_pdb = False
active_pdb: Any = None
_config: pytest.Config | None = None
aborted = False
signals_seen: list[str] = []
# Copies of the original stdout/stderr, taken at configure time: pytest's fd capture
# temporarily replaces fds 1/2 during test phases.
orig_fds: dict[str, int] = {}


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} t+{time.monotonic() - T0:8.3f}s [{os.getpid()}] {msg}\n"
    with _lock:
        os.write(_log_fd, line.encode(errors="replace"))


def _cmd(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        return f"<{args[0]} failed: {e!r}>"


def _fd_target(fd: int) -> str:
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError as e:
        return f"<{e.strerror}>"


def _ancestors() -> list[tuple[int, str]]:
    """(pid, comm) of all ancestors, closest first."""
    res = []
    pid = os.getppid()
    while pid > 1:
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read()
        except OSError:
            break
        comm = stat[stat.index("(") + 1:stat.rindex(")")]
        res.append((pid, comm))
        pid = int(stat[stat.rindex(")") + 2:].split()[1])
    return res


def _cpu_seconds() -> float:
    with open("/proc/self/stat") as f:
        fields = f.read().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def _try_write(fd: int) -> str:
    try:
        os.write(fd, f"[probe] write test on fd {fd} t+{time.monotonic() - T0:.1f}s\n".encode())
        return "ok"
    except OSError as e:
        return f"{type(e).__name__} errno={e.errno} {e.strerror}"


def _redirect() -> None:
    """Point fds 0/1/2 and pytest's saved copies of them away from the terminal/Jenkins log."""
    out = os.open(os.path.join(STATE_DIR, "pytest.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    null = os.open(os.devnull, os.O_RDONLY)
    for f in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        try:
            if f is not None:
                f.flush()
        except Exception as e:
            log(f"flush {f!r} failed: {e!r}")
    capman = _config.pluginmanager.getplugin("capturemanager") if _config else None
    mc = getattr(capman, "_global_capturing", None)
    saved = []
    for cap, src in ((getattr(mc, "in_", None), null), (getattr(mc, "out", None), out),
                     (getattr(mc, "err", None), out)):
        if cap is not None and hasattr(cap, "targetfd_save"):
            os.dup2(src, cap.targetfd_save)
            saved.append(cap.targetfd_save)
    os.dup2(null, 0)
    os.dup2(out, 1)
    os.dup2(out, 2)
    os.close(out)
    os.close(null)
    log(f"redirected fds 0/1/2 and pytest saved fds {saved} to pytest.log")


def _continue_pdb() -> None:
    """Make the pending debugger prompt (running in its own thread) return "continue"."""
    app = active_pdb.pt_app.app
    log(f"pdb continue: app.is_running={app.is_running}")
    if app.loop is not None and app.is_running:
        app.loop.call_soon_threadsafe(lambda: app.exit(result="continue"))


def _on_signal(signum: int, frame: Any) -> None:
    # Hide this frame from pytest's report: the KeyboardInterrupt raised here must
    # be reported at the interrupted line, as with a real Ctrl+C (whose default
    # handler is C code, with no Python frame).
    __tracebackhide__ = True
    global aborted
    name = signal.Signals(signum).name
    signals_seen.append(name)
    log(f"SIGNAL {name} #{signals_seen.count(name)} phase={phase} in_pdb={in_pdb} "
        f"ancestors_alive={[p for p, _ in _ancestors()]}")
    if signum == signal.SIGINT:
        log("SIGINT -> KeyboardInterrupt (default behavior)")
        raise KeyboardInterrupt
    if aborted:
        log("already aborted: ignored")
        return
    aborted = True
    _redirect()
    os.write(1, f"\n===== ABORT ({name}) in phase {phase} =====\n".encode())
    if _config is not None and _config.pluginmanager.has_plugin("pdbinvoke"):
        # No more debugger after an abort.
        _config.pluginmanager.unregister(name="pdbinvoke")
        log("pdbinvoke unregistered")
    if phase.startswith("teardown") or phase == "sessionfinish":
        log("abort in teardown: teardown goes on")
    elif in_pdb:
        _continue_pdb()
    else:
        log("abort outside teardown: KeyboardInterrupt")
        raise KeyboardInterrupt


def _watcher(ancestors: list[tuple[int, str]]) -> None:
    alive = {pid: comm for pid, comm in ancestors}
    last_cpu = _cpu_seconds()
    tick = 0
    while True:
        time.sleep(0.2)
        tick += 1
        for pid, comm in list(alive.items()):
            if not os.path.exists(f"/proc/{pid}"):
                log(f"ANCESTOR GONE pid={pid} comm={comm}")
                del alive[pid]
        # Once a signal arrived: probe the original fds and the CPU usage every 2s
        # (a debugger spinning on EOF/EIO would show up as high CPU).
        if signals_seen and tick % 10 == 0:
            cpu = _cpu_seconds()
            log(f"heartbeat phase={phase} in_pdb={in_pdb} cpu_last_2s={cpu - last_cpu:.2f}s "
                f"orig_stdout={_try_write(orig_fds['stdout'])} orig_stderr={_try_write(orig_fds['stderr'])} "
                f"fd0={_fd_target(0)} fd1={_fd_target(1)} fd2={_fd_target(2)}")
            last_cpu = cpu


def pytest_configure(config: pytest.Config) -> None:
    global _config
    _config = config
    log("=" * 70)
    log(f"probe start, cmdline={os.getpid()}: {open('/proc/self/cmdline').read().replace(chr(0), ' ')}")
    log(f"pid={os.getpid()} ppid={os.getppid()} pgid={os.getpgrp()} sid={os.getsid(0)}")
    log(f"isatty: 0={os.isatty(0)} 1={os.isatty(1)} 2={os.isatty(2)}")
    log(f"fds: 0={_fd_target(0)} 1={_fd_target(1)} 2={_fd_target(2)}")
    orig_fds["stdout"] = os.dup(1)
    orig_fds["stderr"] = os.dup(2)
    for var in ("JENKINS_URL", "BUILD_TAG", "BUILD_ID", "JENKINS_NODE_COOKIE", "JENKINS_SERVER_COOKIE",
                "WORKSPACE", "STY", "TERM", "DEBUG"):
        log(f"env {var}={os.environ.get(var)!r}")
    ancestors = _ancestors()
    log(f"ancestors: {ancestors}")
    # Only the sessions of pytest and its closest ancestors (the job's processes).
    sids = sorted({os.getsid(0)} | {os.getsid(pid) for pid, _ in ancestors[:4]})
    log(f"process tree (sessions {sids}):\n"
        + _cmd(["ps", "--forest", "-o", "pid,ppid,pgid,sid,tty,stat,cmd", "-s", ",".join(map(str, sids))]))
    log("screen -ls:\n" + _cmd(["screen", "-ls"]))
    with open(os.path.join(STATE_DIR, "pytest.pid"), "w") as f:
        f.write(f"{os.getpid()}\n")
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGUSR1):
        signal.signal(sig, _on_signal)
    log(f"handlers installed; SIGPIPE disposition={signal.getsignal(signal.SIGPIPE)}")
    atexit.register(lambda: log(f"atexit; signals seen: {signals_seen}"))
    threading.Thread(target=_watcher, args=(ancestors,), daemon=True).start()

    # Copy root logging records (fixture/test progress) into probe.log.
    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log(f"logging {record.levelname}: {record.getMessage()}")
    logging.getLogger().addHandler(_Handler(level=logging.INFO))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Any:
    global phase
    phase = f"setup:{item.name}"
    log(f"phase -> {phase}")
    try:
        return (yield)
    finally:
        log(f"phase {phase} done")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Any:
    global phase
    phase = f"call:{item.name}"
    log(f"phase -> {phase}")
    try:
        return (yield)
    finally:
        log(f"phase {phase} done")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> Any:
    global phase
    phase = f"teardown:{item.name}"
    log(f"phase -> {phase}")
    try:
        return (yield)
    finally:
        log(f"phase {phase} done")


def pytest_enter_pdb(config: pytest.Config, pdb: Any) -> None:
    global in_pdb, active_pdb
    in_pdb = True
    active_pdb = pdb
    log(f"enter pdb ({type(pdb).__name__})")
    if "_prompt" not in vars(pdb):
        # A dead terminal makes the prompt raise EOFError or OSError (EIO) from its
        # thread: turn that into "continue" instead of quit / INTERNAL_ERROR.
        orig = pdb._prompt

        def _prompt() -> str:
            try:
                return orig()
            except (EOFError, OSError) as e:
                log(f"pdb prompt failed ({e!r}): continue")
                return "continue"
        pdb._prompt = _prompt


def pytest_leave_pdb(config: pytest.Config, pdb: Any) -> None:
    global in_pdb, active_pdb
    in_pdb = False
    active_pdb = None
    log("leave pdb")


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> Any:
    # tryfirst wrapper: set the phase before the runner tears down the remaining
    # fixtures (that is where the teardown happens after a KeyboardInterrupt).
    global phase
    phase = "sessionfinish"
    log(f"sessionfinish exitstatus={exitstatus!r}")
    try:
        return (yield)
    finally:
        with open(os.path.join(STATE_DIR, "status"), "w") as f:
            f.write(f"{int(session.exitstatus)}\n")
        log(f"sessionfinish done, final exitstatus={session.exitstatus!r}")


def pytest_unconfigure(config: pytest.Config) -> None:
    log(f"unconfigure; signals seen: {signals_seen}")
