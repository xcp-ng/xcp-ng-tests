"""
Clean abort of pytest in Jenkins jobs.

The problem
-----------

When a Jenkins build is aborted, Jenkins stops the step running pytest within seconds: by default pytest dies on the
SIGTERM, and the fixture teardowns don't run, leaving the test hosts dirty. Jenkins can't be made to wait for a
teardown that takes minutes, so the teardown has to outlive the Jenkins step, and the job has to wait for it in its
post section.

What this plugin does
---------------------

Jenkins aborts a step in two waves, ~5s apart: a SIGTERM, then the forced kill of the step's processes (another
SIGTERM to pytest, and in DEBUG mode a SIGHUP when Jenkins kills the screen session pytest runs in). Jenkins
displays the step's output until the second wave. pytest survives both waves:

- On the first one, it stops the session (no more tests) and interrupts the current test, like Ctrl+C would, so
  that the fixtures are torn down. If the debugger is open, it continues instead. A fixture setup in progress is
  completed first (interrupting it would skip its teardown), and a teardown in progress goes on.
- It keeps writing its output to a file the post step can read (see `_keep_output()`), and records in the "abort"
  file the offset at which Jenkins stopped displaying it.
- Once the post step runs `scripts/jenkins_pytest.py monitor`, a new SIGTERM is a second abort (the post step is
  aborted, or times out): pytest interrupts the teardown.

Contract with the Jenkins job
-----------------------------

The plugin is opt-in: conftest.py registers it only when JENKINS_PYTEST_ABORT=1. A job sets this variable only if:

- its post section (always) runs `scripts/jenkins_pytest.py monitor`, with a timeout: the monitor displays the rest
  of the output, and waits for pytest to exit,
- the resources used by the tests (e.g. the lock on the test hosts) are held for both the test step and the post
  section,
- in DEBUG mode, it runs pytest with `scripts/jenkins_pytest.py run NAME`, so that one can attach to its debugger.

The state shared with `scripts/jenkins_pytest.py` is in a directory per build, see `state_dir()`.
"""

from __future__ import annotations

import pytest

import os
import signal
import sys
from pathlib import Path
from pdb import Pdb
from types import FrameType

from typing import Any, Generator

def enabled() -> bool:
    return os.environ.get("JENKINS_PYTEST_ABORT") == "1" and "BUILD_NUMBER" in os.environ

def state_dir() -> Path:
    """
    Directory shared with `scripts/jenkins_pytest.py`, one per build.

    It's in the temporary directory Jenkins provides next to the workspace (<workspace>@tmp), and contains:
    - pytest.pid: the PID of pytest,
    - abort: after an abort, "<offset> <path>": the file of the output of pytest, and where Jenkins stopped
      displaying it,
    - output.log: a link to the log file of the Jenkins step (deleted by Jenkins at the end of the step),
    - monitor.pid: the PID of the monitor of the post step, while it runs,
    - in DEBUG mode, screen.log: the output of the screen session.
    """
    tmp = os.environ.get("WORKSPACE_TMP") or f"{os.environ['WORKSPACE']}@tmp"
    return Path(tmp) / f"pytest-{os.environ['BUILD_NUMBER']}"

def pid_alive(path: Path) -> bool:
    """Whether the process whose PID is in the file is running."""
    try:
        os.kill(int(path.read_text()), 0)
    except (OSError, ValueError):
        return False
    return True

class AbortHandler:
    """pytest plugin handling the abort of the Jenkins job: see the module docstring."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.dir = state_dir()
        self.session: pytest.Session | None = None
        # The debugger, while it's open
        self.pdb: Pdb | None = None
        # Number of fixtures being set up: a fixture sets up its dependencies first, so setups are nested
        self.setup_depth = 0
        self.in_teardown = False
        self.aborted = False
        self.second_wave = False
        # After an abort: the file of the output, readable by the post step, and our own descriptor to write to it
        self.output: Path | None = None
        self.output_fd = -1

        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "pytest.pid").write_text(f"{os.getpid()}\n")
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGHUP, self._on_signal)

    def _on_signal(self, signum: int, frame: FrameType | None) -> None:
        # The KeyboardInterrupt raised from here must be reported at the interrupted line, as with a real Ctrl+C
        # (whose handler has no Python frame), not in this handler.
        __tracebackhide__ = True

        if not self.aborted:
            self.aborted = True
            self._first_abort()
        elif signum == signal.SIGTERM and pid_alive(self.dir / "monitor.pid"):
            # The post step is running: this is a second abort
            self._write("\n===== Jenkins abort: interrupting the teardown =====\n")
            raise KeyboardInterrupt
        elif not self.second_wave:
            # Second wave of the first abort: Jenkins stops displaying the output now. Later signals may come
            # several seconds after (e.g. the SIGHUP when Jenkins kills the screen session in DEBUG mode).
            self.second_wave = True
            self._save_offset()

    def _first_abort(self) -> None:
        __tracebackhide__ = True

        try:
            self._keep_output()
        except OSError:
            # The teardown matters more than its display
            pass
        self._write("\n===== Jenkins abort =====\n")
        # Saved again at the second wave: this one is in case there is none
        self._save_offset()

        # No more tests, and no more debugger
        if self.session is not None:
            self.session.shouldstop = "Jenkins abort"
        if self.config.pluginmanager.has_plugin("pdbinvoke"):
            self.config.pluginmanager.unregister(name="pdbinvoke")

        if self.pdb is not None:
            # Make the debugger "continue", so that the test ends and its fixtures are torn down
            app = getattr(getattr(self.pdb, "pt_app", None), "app", None)
            if app is not None:
                # IPython's TerminalPdb (see pytest.ini): its prompt runs in its own thread, make it return "continue"
                if app.is_running:
                    app.loop.call_soon_threadsafe(lambda: app.exit(result="continue"))
            else:
                # Plain pdb: queue "continue", and interrupt the input it waits for. Its loop catches the
                # KeyboardInterrupt, and runs the queued command before reading input again.
                self.pdb.cmdqueue.append("continue")
                raise KeyboardInterrupt
        elif not self.in_teardown and self.setup_depth == 0:
            raise KeyboardInterrupt
        # Else, a teardown in progress goes on. And a fixture setup in progress completes: interrupting it would skip
        # its teardown, only registered once it reaches its yield. The interruption is raised before the next fixture
        # setup, or before the test (see the hooks below).

    def _keep_output(self) -> None:
        """Make sure the output of pytest can still be written, and read by the post step."""
        capturing = getattr(self.config.pluginmanager.getplugin("capturemanager"), "_global_capturing", None)
        screen_log = self.dir / "screen.log"
        if screen_log.exists():
            # DEBUG mode: the output of pytest is the terminal of a screen session, which Jenkins kills at the second
            # wave. From now on, write to the log of the screen session instead (teetty appends to it too).
            for f in (sys.__stdout__, sys.__stderr__):
                try:
                    if f is not None:
                        f.flush()
                except OSError:
                    pass
            self.output = screen_log
            self.output_fd = os.open(screen_log, os.O_WRONLY | os.O_APPEND)
            # Redirect fds 1/2, and pytest's saved copies of them: its output capture copies them back onto
            # fds 1/2 between test phases.
            for fd, name in ((1, "out"), (2, "err")):
                os.dup2(self.output_fd, fd)
                saved = getattr(getattr(capturing, name, None), "targetfd_save", None)
                if saved is not None:
                    os.dup2(self.output_fd, saved)
        else:
            # The output of pytest is the log file of the Jenkins step. Keep writing to it, and keep a link to it,
            # as Jenkins deletes it at the end of the step. During a test phase, fd 1 is pytest's capture file: the
            # original is its saved copy.
            out = getattr(getattr(capturing, "out", None), "targetfd_save", 1)
            # Shares its offset with the output of pytest: what we write doesn't overwrite it
            self.output_fd = os.dup(out)
            self.output = self.dir / "output.log"
            try:
                os.link(os.readlink(f"/proc/self/fd/{out}"), self.output)
            except OSError:
                # Readable while pytest runs
                self.output = Path(f"/proc/{os.getpid()}/fd/{self.output_fd}")

    def _write(self, msg: str) -> None:
        try:
            os.write(self.output_fd, msg.encode())
        except OSError:
            pass

    def _save_offset(self) -> None:
        """Record the current size of the output: the post step displays it from there."""
        if self.output is None:
            return
        try:
            (self.dir / "abort").write_text(f"{self.output.stat().st_size} {self.output}")
        except OSError:
            pass

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.session = session

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(self, fixturedef: pytest.FixtureDef[Any],
                             request: pytest.FixtureRequest) -> Generator[None, Any, Any]:
        # Aborted during a previous fixture setup: interrupt before this one. A fixture sets up its dependencies
        # before its body runs, so this never interrupts a fixture body. Except for a body calling
        # request.getfixturevalue(): keep such calls before any side effect.
        if self.aborted:
            raise KeyboardInterrupt(f"Jenkins abort, before the setup of {fixturedef.argname}")
        self.setup_depth += 1
        try:
            return (yield)
        finally:
            self.setup_depth -= 1

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, Any, Any]:
        # Aborted during the setup of the last fixture: interrupt before the test
        if self.aborted:
            raise KeyboardInterrupt("Jenkins abort, after the setup")
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_teardown(self, item: pytest.Item, nextitem: pytest.Item | None) -> Generator[None, Any, Any]:
        self.in_teardown = True
        try:
            return (yield)
        finally:
            self.in_teardown = False

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self) -> None:
        # After an interruption, the remaining fixtures are torn down from here
        self.in_teardown = True

    def pytest_enter_pdb(self, config: pytest.Config, pdb: Pdb) -> None:
        self.pdb = pdb
        # IPython's TerminalPdb only (plain pdb has no _prompt). When its terminal dies (Jenkins kills the screen
        # session in DEBUG mode), the prompt fails with EOFError or OSError: continue instead of quitting, or crashing
        # pytest.
        prompt = getattr(pdb, "_prompt", None)
        if prompt is None or "_prompt" in vars(pdb):
            return

        def safe_prompt() -> str:
            try:
                return prompt()
            except (EOFError, OSError):
                return "continue"
        setattr(pdb, "_prompt", safe_prompt)

    def pytest_leave_pdb(self, config: pytest.Config, pdb: Pdb) -> None:
        self.pdb = None
