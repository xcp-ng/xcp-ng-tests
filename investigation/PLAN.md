# Plan: clean Jenkins job cancellation

See `README.md` for the goal, constraints and decisions.

Useful facts from the code:

- `jobs.py run` uses `os.execvp`, so the pytest process takes over the PID of `jobs.py`.
  There is no Python parent in the middle.
- `pytest.ini` sets `--pdbcls=IPython.terminal.debugger:TerminalPdb`, so the debugger reads input through prompt_toolkit, not plain `input()`.

## How Jenkins aborts (confirmed by the probe runs, see FINDINGS.md)

- No SIGKILL: pytest survives the abort of its `sh` step and is reparented to PID 1. No `setsid`, no `JENKINS_NODE_COOKIE` tricks.
- Jenkins signals processes by environment (cookie), not by process group.
  It reaches pytest inside `screen`/`teetty` directly (run 3), and it also reaches pytest **when the post step is aborted** (run 4). The cookie is probably per build.
- Every abort comes in **two waves**, ~5–6s apart:
  - first abort: wave 1 is SIGTERM #1 to pytest; wave 2 (+5.3s) kills the `sh` parents, sends SIGTERM #2 to pytest, and in DEBUG mode kills `screen`/`teetty` (SIGHUP to pytest).
  - second abort (during post): wave 1 is SIGTERM to pytest at once; wave 2 (+6.4s) is SIGTERM to the post script.
- Post starts ~11s after the first abort, after the first abort's wave 2, in every run so far.
- Jenkins stops displaying a step's output ~5s after its abort.
- History of `teetty`: it replaced a pipe into `tee`, because killing `tee` took pytest down with it (broken pipe).
  A dead `teetty` causes the same kind of problem on a pseudo-terminal: SIGHUP, then I/O errors on read and write.

## Output after an abort: Jenkins displays it until wave 2, the post step displays the rest

- **No DEBUG:** pytest's output is the step's `jenkins-log.txt`, a regular file. pytest keeps writing to it (it never fails), and at wave 1 hard-links it into its state directory, because Jenkins deletes it at the end of the step.
- **DEBUG:** the terminal dies at wave 2, so at wave 1 pytest redirects fds 0/1/2, and pytest's saved copies of them (the capture manager's `FDCapture.targetfd_save`), to `screen.log`. That's the file `teetty` appends to and `run` keeps relaying to the step console until wave 2 (`run` and its `tail` ignore SIGTERM).
- At each wave, pytest saves the size of that file in `abort` (`<offset> <path>`). The post step `tail`s it from the last offset until pytest exits.
- Known limit (chosen: option 2): what pytest writes between Jenkins's last read of the step log and wave 2 is displayed nowhere (it is still in the file). In the simulation, that's the one line written at the exact moment of wave 2.

pdb is left through the **api-continue** method plus a **safe prompt** (FINDINGS 2.3).

## Phase 1: Jenkins probe (first, can run alongside phase 2) — done (FINDINGS runs 1–4)

Add temporary diagnostics that log with timestamps to `$WORKSPACE/.jenkins-pytest/probe.log`, then abort by hand, once with DEBUG=false and once with DEBUG=true. Log:

- every signal received by pytest, in order: SIGTERM, SIGHUP, how many, and when;
- the process tree at startup: `ps -o pid,ppid,pgid,sid,cmd`;
- in DEBUG mode, the order of events: does pytest get SIGTERM before or after `screen`/`teetty` die? Poll their PIDs from a thread.
- whether writing to the original stdout fails after the abort (I/O error, broken pipe) or silently succeeds;
- whether pytest is still alive when `post` starts and when the build ends.

## Phase 2: local experiments (no Jenkins) — done

Use a host-less copy of `tests/storage/test_debug_jenkins.py` and send signals with `kill`.

1. `KeyboardInterrupt` raised during setup or the test call: do the fixture teardowns still run, and what is the exit code?
2. A second `KeyboardInterrupt` during a teardown: does it stop only the current finalizer or all of them? Does pytest exit cleanly?
3. Leaving pdb (TerminalPdb, in `screen` + `teetty`). Compare three ways:
   - **Ctrl+D / EOF**: may come for free with the stdin → `/dev/null` redirect, but it **has already failed in a previous test**. Treat it as a hypothesis.
     prompt_toolkit may keep its own handle on the terminal, block instead of reading EOF, or loop on errors.
     pdb's `do_EOF` quits (BdbQuit), and pytest turns that into "Quitting debugger" (`pytest.exit`).
     Check that the teardowns still run, and how prompt_toolkit reacts to stdin being swapped while it waits for input.
   - Validate the chosen method in the real setup (`screen` + `teetty`, then on Jenkins), not only in a plain terminal.
     Keep a second method ready as a fallback.
   - **pdb/pytest API**: from the handler, queue `continue` on the active debugger (through a hook or a `--pdbcls` subclass) and get the prompt to return.
   - **Typing `continue`**: inject it into the terminal. Last resort, because it depends on the terminal plumbing.
4. fd redirect under `teetty`: kill `teetty` (and `screen`) while pytest is writing.
   Check that SIGHUP plus the redirect avoid any I/O error in the teardown, and that `pytest.log` contains the full output, including tracebacks and the final summary.

## Phase 3: pytest side (`lib/jenkins.py`, registered from `conftest.py`) — implemented

Active only when `JENKINS_URL` / `BUILD_NUMBER` are set. Files go in `$WORKSPACE_TMP/pytest-$BUILD_NUMBER/` (Jenkins' `<workspace>@tmp`, one directory per build): `pytest.pid`, `abort`, `output.log` (link to the step log), `monitor.pid`, and in DEBUG mode `screen.log`/`screen.status`.

- At session start: write `pytest.pid`. Ignore SIGHUP until the handlers below are installed.
- **First abort signal** (SIGTERM or SIGHUP):
  1. Keep the output readable by the post step (link the step log, or redirect to `screen.log` in DEBUG mode, see above), write a `===== Jenkins abort =====` marker, save the offset in `abort`.
  2. Stop the session (`session.shouldstop`: no more tests) and turn off pdb.
  3. If pytest is not in a teardown (collection, setup, test call, or in pdb), interrupt it like a SIGINT (`KeyboardInterrupt`), or use the pdb exit picked in phase 2.3.
     If it is in a teardown, let the teardown continue.
- **Later SIGTERMs/SIGHUPs:** tell the first abort's duplicates apart from a second abort **by whether post has started**:
  - if `monitor.pid` exists (written by the monitor when post starts, see phase 4) and that process is alive: **second abort** → `KeyboardInterrupt`, which interrupts the teardown at once;
  - otherwise: the first abort's wave 2 (SIGTERM #2, SIGHUP from the dead terminal). Save the offset in `abort` again: Jenkins stops displaying the output now.

  Why it holds: the duplicates arrive ~5.3s after the abort, and post starts ~11s after it (FINDINGS runs 1–4). A second abort can only happen once post is running.
  Without this, the second abort is only handled by the monitor, ~6.4s late and racing with the lock release (run 4).
- **SIGINT:** keep the default `KeyboardInterrupt` (manual interruption).
- **Reported location:** the handler sets `__tracebackhide__ = True`, so pytest reports the interrupted line, not the handler (FINDINGS run 2).

## Phase 4: `scripts/jenkins_pytest.py` — implemented

- `run NAME CMD...` (DEBUG only, replaces `detash.sh` for this job): `screen -D -m -S NAME teetty --out screen.log -- CMD`, prints the `ssh … screen -r NAME` hint, relays `screen.log` with `tail --pid`, exits with the status of CMD.
  Ignores SIGTERM (and so does its `tail`): pytest gets the abort from Jenkins directly (run 3), and the console keeps flowing until wave 2 kills `screen`.
- `monitor` (post/always): if there was an abort (`abort` exists), writes `monitor.pid`, then `tail`s the output from the saved offset until pytest exits.
  No SIGINT/SIGKILL fallback: pytest handles the second abort itself, in wave 1 (phase 3).
  Trade-off: a pytest that ignores `KeyboardInterrupt` (e.g. stuck in a blocking call Python can't interrupt) would outlive the build.

## Phase 5: Jenkins files (`investigation/flaky-tests`, `investigation/debug_env.groovy`) — written

- `flaky-tests`:
  - Move `lock` into the stage `options`.
  - Keep the `sh` line, with `${env.debug_prefix}` now pointing at `jenkins_pytest.py run --screen …`.
  - Add `post { always { timeout(15 min) { sh 'scripts/jenkins_pytest.py monitor' } } }`. This is the **only** timeout: when it fires, Jenkins aborts the post step, which is a second abort (pytest interrupts its teardown).
- `debug_env.groovy`:
  - Drop the `detash.sh` PATH lookup and `DETASH_ON_QUIT`.
  - Set `debug_prefix="scripts/jenkins_pytest.py run --screen jenkins-${BUILD_NUMBER} --"`, and keep `--pdb` as the suffix.
    Not `BUILD_TAG`: it contains the job name, which can contain spaces ("VML tests").

## Phase 6: validation matrix (on Jenkins, `-k jenkins_debug`)

| DEBUG | abort at | second abort | expected |
|---|---|---|---|
| false | no abort | – | test fails, teardown runs, normal FAILURE |
| false | during setup | no | interrupted; the `/tmp/dirty` teardown runs if the fixture was set up |
| false | during the test (sleep) | no | test interrupted; teardown streamed by `post`; `/tmp/dirty` removed; ABORTED |
| false | during teardown | no | teardown completes and is streamed in `post` |
| false | during teardown | yes | teardown interrupted by pytest itself as soon as the second abort arrives; pytest gone before Jenkins ends post |
| false | during the test (sleep) | yes | same: the teardown started by the first abort is interrupted at the second |
| true | no abort | – | pdb opens; attach and `continue` by hand works |
| true | during the test (sleep) | no | no pdb opens; teardown runs |
| true | while in pdb | no | pdb exits on its own; teardown streamed |
| true | while in pdb | yes | teardown interrupted at the second abort; screen session gone |
| any | – | post timeout (15 min) | same as a second abort |

Also check the host after each run: `/tmp/dirty`, and that the next job can take the lock.

## Phase 7: cleanup

- Remove the probe diagnostics, or keep them behind an env var.
- Decide whether `detash.sh` stays for other jobs or is deleted.
