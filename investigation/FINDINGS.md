# Findings

Results of the experiments from `PLAN.md`. Each entry says how it was observed.

## Local (phase 2)

Setup: `investigation/experiments/test_local_debug.py` (host-less copy of the debug test, durations from `CALL_S` / `TEARDOWN_S`), probe plugin loaded with `-p jenkins_probe`.

### 2.1 KeyboardInterrupt during the test call → teardown runs ✅

`kill -INT` during the call phase: the call stops, the session ends with `INTERRUPTED` (exit code 2), and the fixture teardown then runs **from `pytest_sessionfinish`**, not from `pytest_runtest_teardown`. The `dirty` marker was removed. The test is reported as "no tests ran", not as failed.

Consequence: "are we in a teardown?" can't rely on `pytest_runtest_teardown` alone. Teardown after an interrupt happens during session finish.

### 2.2 KeyboardInterrupt during the teardown → teardown stops (one fixture) ⚠️ partial

`kill -INT` during the fixture teardown loop: the teardown stops at once, the session ends with `INTERRUPTED`, and `dirty` is left behind. This is the expected second-abort behavior.
Still to test: several fixtures. Does an interrupt in one finalizer skip the others?

### 2.3 Leaving pdb on abort → "api-continue" + safe prompt ✅

Harness: `experiments/run_pdb_exit.sh <method> [none|kill-tty|tty-first]` with plugin `experiments/pdb_exit.py`. It runs `pytest --pdb` under `screen` + `teetty`, waits for pdb, sends SIGTERM, and optionally kills the terminal (`screen -X quit`) 0.5s after (`kill-tty`) or 0.5s before (`tty-first`) the SIGTERM.

Useful internals: IPython's `TerminalPdb.cmdloop` runs the prompt **in a worker thread** (`thread_executor.submit(self._prompt).result()`). It only catches `EOFError`, which it turns into the `EOF` command, i.e. quit. pytest's `pytest_enter_pdb(config, pdb)` hook gives access to the debugger instance.

| method | how | result (terminal alive) |
|---|---|---|
| none (control) | – | stays in pdb forever |
| **api-continue** | `pdb.pt_app.app.loop.call_soon_threadsafe(app.exit, result="continue")` | ✅ pdb continues, teardown complete, exit 1 |
| api-interrupt | raise `KeyboardInterrupt` in the main thread | ❌ IPython catches it (`--KeyboardInterrupt--`) and prompts again |
| eof | `dup2(/dev/null, 0)` | ❌ hangs: the prompt keeps waiting on the old terminal (most likely the event loop watches it directly) |
| type | `screen -X stuff "continue^M"` | ✅ like api-continue, but needs the terminal plumbing |

The **terminal dying** is the hard part (`kill-tty`, `tty-first`):

- Without a redirect, pytest dies at the first teardown write (SIGHUP default action, or EIO), and the dirty marker is left behind.
- With the redirect (fds 0/1/2 **and** pytest's saved copies, the capture's `targetfd_save`, see below), the teardown completes. But in `tty-first`, 2 out of 3 runs ended with `INTERNAL_ERROR`:
  - the prompt thread sees the dead terminal **before** the main thread handles SIGHUP;
  - prompt_toolkit gets EOF and redraws, and the flush fails with `OSError(EIO)`;
  - `cmdloop` doesn't catch `OSError`, so it escapes into pytest.
- **Fix ("safe prompt")**: in `pytest_enter_pdb`, wrap the instance's `_prompt` so that `EOFError`/`OSError` return `"continue"`.
  This also covers a terminal that dies with no SIGTERM at all.

With api-continue + redirect + safe prompt: **12/12 clean runs** (4 each for `none`, `kill-tty`, `tty-first`). Every run has exit status `TESTS_FAILED`, the teardown completed, the dirty marker was removed, and the full output (including the final summary) is in the log file.

Notes:

- `sys.stdout.flush()` before the redirect can fail with EIO when the terminal is already dead. Catch it. Any data still buffered goes to the new file on the next flush.
- After the redirect, someone attached to `screen` no longer sees the output. That's acceptable: it's only after an abort.
- The `bash -c '…; echo $? > status'` wrapper dies with the terminal, so **pytest must write its own status file**.
- Not tested yet: an error during a teardown *after* the abort would open pdb again. Since stdin is `/dev/null`, the safe prompt probably turns that into `continue`, but pdb should be explicitly disabled after an abort (e.g. unregister the `pdbinvoke` plugin).

### 2.4 Terminal dies while pytest is in pdb (DEBUG) → pytest dies mid-teardown ❌

Setup: `screen_run.sh` → `screen` → `teetty` → `pytest --pdb`, test fails, pdb open. Then:

1. SIGTERM to the launcher and to pytest: pytest logs it (the probe ignores it) and stays in pdb. CPU is idle and nothing else happens.
2. SIGTERM to `teetty` and `screen`:
   - pytest gets **SIGHUP**, and pdb exits on its own;
   - the session ends with **`INTERNAL_ERROR` (3)**, the teardown logs its first line (`[00/4]`), then **the process dies**;
   - `dirty` is left behind, and the traceback is lost (the terminal is gone, `screen.log` stops at the `ipdb>` prompt).

This matches the earlier failed test: a dead terminal does get pytest out of pdb, but as a crash, not as a clean `continue`. Most likely the writes or reads on the hung-up pty fail. Confirmed in 2.3: the prompt thread fails with EIO and the error escapes as `INTERNAL_ERROR`.

Consequence: the redirect (core rule in `PLAN.md`) has to happen **before** the terminal dies, or pytest must survive a hung-up terminal. Whether Jenkins kills `screen`/`teetty` at all, and in which order relative to the SIGTERM to pytest, is the key question for the probe.

### pytest's fd capture changes fds 1/2 during test phases ⚠️

With the default `--capture=fd`, fd 1/2 point to a deleted temp file during setup/call/teardown. pytest copies its saved originals back onto them in between phases. An `os.dup2(logfile, 1)` done during a phase would be undone at the next suspend. The redirect also has to replace pytest's saved copies of the originals, i.e. the capture manager's `targetfd_save`, or go through a different mechanism. This is not a problem in DEBUG mode while pdb is open, because capture is suspended then.

## Jenkins (phase 1)

### Run 1: `jenkins.log.1`, DEBUG=false, aborted during the 60s test sleep

The probe was observe-only at that time (SIGTERM was logged and ignored), so **the abort did not interrupt the test**. That was a mistake in the probe, fixed afterwards. The process facts are still valid:

- pytest runs in the job's session/process group (pgid = sid = 5219, the durable-task `sh`), with stdin `/dev/null` and stdout/stderr going to `…@tmp/durable-…/jenkins-log.txt` (not a tty, not a pipe).
- Env: `JENKINS_URL`, `BUILD_TAG`, `JENKINS_NODE_COOKIE`, `JENKINS_SERVER_COOKIE` are all set.
- **Abort: SIGTERM #1** to pytest at once ("Sending interrupt signal to process").
- Jenkins **kept showing the output for ~5s** after the abort, then stopped.
- **SIGTERM #2 ~5.3s later**, at the same time as both `sh` parents died.
- **No SIGKILL.** pytest was reparented to PID 1 and ran until the end, through the whole 60s teardown. Writes to the original stdout never failed (the file is just no longer displayed).
- Its ssh ControlMaster processes (`ssh: …/control-<pytest pid>…`) survived too.
- **post started ~10s after the abort**, with pytest alive. The lock in the stage `options` was **released only after post**, as intended.
- `post_probe.sh` failed with exit code 1: `screen -ls` returns 1 when there is no session. Fixed.

### Probe fixed after run 1

The probe now applies the planned behavior on the first SIGTERM/SIGHUP:

- redirect to `pytest.log`;
- pdb api-continue + safe prompt;
- otherwise `KeyboardInterrupt` unless in a teardown;
- unregister `pdbinvoke`;
- `status` file written by pytest.

Checked locally:

- non-DEBUG, Jenkins-like sequence (SIGTERM, then SIGTERM 5s later) during the test sleep: the test is interrupted, the teardown runs from session finish, status 2, dirty marker removed, output in `pytest.log`.
- DEBUG (`screen_run.sh`, `--pdb`): SIGTERM to the launcher and pytest while in pdb, then `screen` killed 5s later plus SIGTERM #2. Result:
  - pdb continued, and the teardown went on through SIGTERM #2 and SIGHUP;
  - writes to the original terminal then failed with EIO, but nothing used it any more;
  - status 1, dirty marker removed, full summary in `pytest.log`.

### Run 2: `jenkins.log.2`, DEBUG=false, fixed probe, aborted during the 60s test sleep ⚠️

The signal and teardown chain works without DEBUG, but **the report doesn't say where the test was interrupted**:

- Without `--full-trace`, pytest prints only where the `KeyboardInterrupt` was raised, i.e. **our signal handler** (`jenkins_probe.py:159`). The test frame is missing.
- The test isn't reported at all ("no tests ran" / "537 deselected"), so nothing shows it was interrupted.
- Root cause: the `KeyboardInterrupt` is raised from **our Python signal handler**, so that frame is the last one and pytest reports its line.
  With a real Ctrl+C, the default handler is C code (no Python frame), so pytest reports the interrupted line.
- **Fix (probe updated):** `__tracebackhide__ = True` in the handler. pytest's `_getreprcrash` picks the last *non-hidden* frame. Checked locally: the report shows `test_local_debug.py:32: KeyboardInterrupt`. Not yet run on Jenkins.
  (Rejected: `fulltrace` at abort gives 635 lines of internals; printing the stack by hand worked around the symptom instead of fixing the cause.)

Otherwise:

- **t+30.4s:** SIGTERM #1. The output goes to `pytest.log`, the test is interrupted by `KeyboardInterrupt`, and the teardown starts from session finish (`[00/60]` at t+31.7s).
- **t+35.7s:** SIGTERM #2, 5.3s after #1 as in run 1, again at the same time as the `sh` parents die. It was ignored and the teardown went on.
- **About 11s after the abort:** post starts, with pytest alive. It waited for pytest to exit (t+91.9s). The teardown completed (`unlink /tmp/dirty`).
- **End:** status 2 (INTERRUPTED), post exit code 0, lock released after post, build `Finished: ABORTED`.

Process details:

- Every process, including pytest **and the post step's `sh`**, is in the **session/process group of the Jenkins agent itself** (`java -jar remoting.jar`, pgid = sid = 5219).
  So Jenkins can't be killing by process group: that would kill the agent. It signals the step's processes one by one, most likely by matching the per-step `JENKINS_NODE_COOKIE` / `JENKINS_SERVER_COOKIE` env vars.
- Consequence for the second abort: aborting the post step should signal only the post step's processes, not pytest. It is up to the monitor to send SIGINT to pytest, as planned. Still to be confirmed by a run.

Not covered by this run:

- **DEBUG mode** (screen + teetty + pdb): the order in which Jenkins kills `screen`/`teetty` versus the SIGTERM to pytest.
- **Second abort** during post.
- **Live output in post**: `post_probe.sh` follows `probe.log` and prints `pytest.log` only at the end. The real monitor must follow `pytest.log` from the `ABORT` marker.
- **Console gap**: between the abort and post (~11s), the console shows nothing useful. pytest could print one line to the original stdout before the redirect ("abort received, teardown continues, see post"), since Jenkins still displays it for ~5s.
- **ssh ControlMaster** processes (`ssh: …/control-<pytest pid>…`) are reparented to PID 1 and outlive the step. That happens without our changes too, and is out of scope.

### Run 3: `jenkins.log.3`, DEBUG=true, aborted while in pdb ✅

- **Before the abort:** the test failed after 60s. The **traceback and the pdb banner were shown in the Jenkins console** (through `screen_run.sh` following `screen.log`), then `ipdb>`.
- **Process layout:** `teetty` puts pytest in **its own session** (pgid = sid = pytest's PID), under `SCREEN` (reparented to PID 1). Its stdin is `/dev/null`, stdout/stderr a pty (`/dev/pts/2`).
- **Abort (t+158.4s):** SIGTERM to the launcher **and SIGTERM #1 directly to pytest** within 0.3s, although pytest is in another session.
  This confirms that Jenkins signals processes by environment (cookie), not by process group. **No need to forward SIGTERM** from the launcher.
  - The handler redirected the output, unregistered `pdbinvoke` and continued pdb (api-continue). The teardown started at once.
- **5.3s later (t+163.7s):** **SIGHUP + SIGTERM #2**, at the same moment as `teetty` and `screen` died. So Jenkins kills them in its second wave, **after** the first SIGTERM to pytest.
  The redirect had already happened, so pytest didn't notice (both signals ignored). The safe prompt wasn't needed in this order, but it still covers the reverse order.
- **Post** started ~11s after the abort and waited for pytest. The teardown completed (`unlink /tmp/dirty`).
  pytest's report is the normal one: `FAILED … ZeroDivisionError`, `1 failed`, status 1. The lock was released after post. Build ABORTED.
- **Cosmetic:**
  - `pytest.log` starts with a stray `ipdb>` (the prompt redrawn as it exits);
  - the launcher printed `Terminated` on its exit;
  - `asyncio` logs `Using selector: EpollSelector` at debug level around pdb.

The DEBUG path works with the planned design. Still to test: the **second abort** (abort the post step: pytest should be left alone by Jenkins and stopped by the monitor's SIGINT).

### Run 4: `jenkins.log.4` + `probe.log` (fetched from the node), DEBUG=false, abort during the sleep, then a second abort during post ⚠️

- **First abort:** same as run 2. SIGTERM #1 at t+11.1s interrupts the test and the teardown runs from session finish. SIGTERM #2 comes 5.3s later, with the `sh` parents dying. Post starts ~11s after the abort.
- **Second abort**, during post, also comes in **two waves**, like the first one:
  - **wave 1 (t+28.7s): pytest receives SIGTERM #3.** So aborting the post step does signal pytest (my run-2 hypothesis was wrong; the cookie is probably per build). The probe treated it like #2 ("already aborted: ignored").
  - **wave 2 (+6.4s, 18:24:15.1): `post_probe.sh` receives SIGTERM.** Its trap sends SIGINT, and pytest stops at once: `KeyboardInterrupt` in the teardown (at `[23/60]`), exit `INTERRUPTED`, gone within 0.25s.
- **Console:** Jenkins stopped displaying the post step ~5s after the second abort (last line `[22/60]` at 18:24:13), so the SIGINT and the exit only appear in `probe.log`. I first misread this as "the trap never ran".
- **Lock:** "Lock released" comes right after the post step is interrupted. pytest exited at 18:24:15.1, so the release and the end of pytest are within about a second of each other. The order can't be told from the console (no timestamps on Jenkins lines).

Consequences for the design:

1. Without changes, the second abort works, but **through the monitor, 6.4s late**, and racing with the lock release.
2. pytest itself receives the second abort at once (wave 1). Handling it in pytest stops the teardown ~6s earlier, before Jenkins even ends post.
   - This needs a way to tell it apart from the first abort's duplicate (#2, ~5.3s after #1).
   - Proposal: **post registers itself** (the monitor writes `monitor.pid` when it starts). pytest treats any SIGTERM received while the monitor is registered as the second abort (`KeyboardInterrupt`).
   - In all runs, the first wave's duplicate arrives before post starts (5.3s vs ~11s).
3. The monitor keeps its SIGINT on SIGTERM (fallback).

## Implementation (phases 3–5): local validation

Code: `lib/jenkins.py` (registered from `conftest.py` when `JENKINS_URL` and `BUILD_TAG` are set), `scripts/jenkins_pytest.py`, `investigation/flaky-tests`, `investigation/debug_env.groovy`.

Simulator: `experiments/simulate_jenkins.sh <debug> <abort at> <second abort>`. It reproduces the waves seen in runs 1–4:

- first abort: SIGTERM to the step, then +5s SIGTERM again and `screen` killed;
- post at +11s;
- second abort: SIGTERM to pytest, then +6s SIGTERM to the monitor.

| case | result |
|---|---|
| A: no DEBUG, abort during the test | ✅ notice on the step console; teardown followed live by the monitor; duplicate ignored; status 2; dirty removed; reported at the test line |
| B: no DEBUG, + second abort | ✅ pytest interrupts its teardown in wave 1, before the monitor's signal; status 2; dirty left (expected). It ends with a raw Python traceback, **same as plain pytest on a second Ctrl+C in a teardown** (one extra frame: `lib/jenkins.py raise KeyboardInterrupt`) |
| C: DEBUG, abort in pdb | ✅ the launcher copies the notice for 2s, then exits quietly (143, no `Terminated`); pdb continued; teardown complete; `FAILED … ZeroDivisionError`; status 1 |
| D: DEBUG, abort in pdb + second abort | ✅ SIGHUP/SIGTERM duplicates ignored; second abort interrupts the teardown |
| E: no abort | ✅ the monitor only prints the status |
| F: monitor timeout (4s) | ✅ SIGINT from the monitor; status 2. *The script's timeout was removed afterwards: the only timeout is now the Jenkins `timeout` of the post step, which acts as a second abort (case B)* |
| abort before pytest's handlers are installed | pytest dies at once (default SIGTERM). Nothing set up, nothing to tear down; the monitor prints "status unknown" |

2.2 (several fixtures): a second abort stops the current finalizer **and skips the remaining ones** (`experiments/test_local_two_fixtures.py`). Plain pytest does the same on a second Ctrl+C. It's the intended meaning of "interrupt the teardown".

Remaining cosmetic issue: a stray `ipdb>` in `pytest.log` after leaving the debugger.

### Minimal rewrite

`lib/jenkins.py` and `scripts/jenkins_pytest.py` were cut down from 369 to 190 lines. What was removed:

- the monitor's SIGINT/SIGKILL fallback;
- the notice on the original output (and the launcher's 2s delay that carried it);
- the phase names and most messages.

Simulated cases A–E give the same results as above. The second abort during a test's teardown (D) is now reported as `1 failed, 1 error` (KeyboardInterrupt in the teardown).

### Output continuity (offset at the second wave)

pytest no longer redirects to `pytest.log`. Jenkins displays the output until wave 2, and the post step continues from the offset saved at wave 2 (see PLAN.md). The simulator now models the step log (`jenkins-log.txt` in `@tmp`, opened with `>`, displayed until wave 2, then deleted):

- **A (no DEBUG):** the step console ends at `[04/10]`, the post step starts at `[05/10]`. Exact junction.
- **C (DEBUG):** the step console ends at `[04/10]`, the post step starts at `[06/10]`, 3/3 runs. The line written at the exact moment of wave 2 is lost (it is still in the file).
  This is the race between Jenkins's last read and wave 2, not `tail`'s polling: `tail -s 0.1` didn't change it.
  On Jenkins the window depends on how often Jenkins polls the step log. Chosen anyway (no repeats) over starting at the wave-1 offset (no loss, ~5s repeated).
- B, D, E: unchanged.
- `teetty --out` appends (checked), so pytest can append to `screen.log` too.

### Review before the Jenkins runs

- **Fixed: the session went on after an abort.** On an abort during a teardown (or in pdb), pytest let the teardown finish and then ran the next test. `--maxfail=1` hid it only for failed tests. Reproduced with `experiments/test_local_two_tests.py` ("SECOND TEST RAN", `2 passed`); fixed with `session.shouldstop` (`Interrupted: Jenkins abort`, `1 passed`).
- **Fixed:** the abort markers were written through pytest's saved fd, which pytest closes at the very end (the number could then be reused). pytest now writes through its own `os.dup`.
- **Hardened:** `OSError` while keeping the output or saving the offset no longer reaches the test or the teardown, and a signal during the first handler no longer saves a bogus offset.
- **Fixed:** the monitor's `tail` ignored SIGTERM, which only `run`'s needs.
- **Removed:** the `status` file (unused), stdin redirection (pdb is left through the API), flushing of the capture buffers.
- Matrix A–E unchanged (B and D end with the raw traceback of a second Ctrl+C, as plain pytest does: no summary line).

## Jenkins runs of the implementation

### Run 5: `jenkins.log.5`, DEBUG=false, aborted during the 60s test sleep ✅

- **Step console:** after "Aborted by … / Sending interrupt signal to process", it shows `===== Jenkins abort =====`, the `live log sessionfinish` header and `[00/60]`…`[04/60]`. That's ~5s of teardown, until wave 2.
- **Post step:** `scripts/jenkins_pytest.py monitor` continues at **`[05/60]`**: an **exact junction** on Jenkins, nothing lost or repeated. It follows the teardown live until `unlink /tmp/dirty`.
- **Report:** `test_debug_jenkins.py:23: KeyboardInterrupt`, the `time.sleep(60)` line (`__tracebackhide__` works on Jenkins). `537 deselected` (an interrupted test isn't counted).
- **End:** the lock was released after the post step, build `Finished: ABORTED`, no `Terminated` or stack trace from the post step.
- Jenkins printed "Click here to forcibly terminate running steps" during post (also in run 4). It's offered while an aborted build still has a running step. Clicking it is **not tested**: it's Jenkins's hard kill, not a second abort.

### Run 6: `scripts/jenkins.log.6`, DEBUG=true, aborted while in pdb ⚠️

- **Debugger:** the attach command printed by `run` (`ssh -t vates@jenkins.lyon.vates.team screen -r debug-38`) worked, and the debugger was usable (`p time`). The banner from `debug_env.groovy` gave `ssh -t ssh-master …`: `NODE_NAME` is the Jenkins node name, not a host. That was already wrong in the original; the banner now points to the line printed by `run`.
- **Abort in pdb:** `===== Jenkins abort =====`, pdb continued, teardown complete (`unlink /tmp/dirty`). Report: `1 failed` + `Jenkins abort`. Lock released after post, build ABORTED.
- **❌ Junction:** the step console ends at `[05/60]` (22:55:20, ~5s after the abort = wave 2), the post step starts at `[11/60]`. **5 lines are displayed nowhere.**
  The offset was saved ~10s after the abort, not at wave 2: the handler saved it again on *every* later signal, and a later one moved it forward (likely the SIGHUP when the screen terminal died, or a further SIGTERM; not logged).
  **Fix:** save the offset only at the first signal after the abort. Unverified on Jenkins.

### Run 7: `jenkins.log.7`, DEBUG=true, abort in pdb, then a second abort during post ✅

- **Junction (fix of run 6):** the step console ends at `[01/60]`, the post step starts at **`[01/60]`**: one line repeated, nothing lost.
  In this run Jenkins stopped displaying the step ~1.5s after the abort, not ~5s. Saving the offset at the first signal after the abort still matched it.
- **Second abort**, ~20 lines into post: pytest interrupts its teardown at once (`===== Jenkins abort: interrupting the teardown =====`, `ERROR at teardown … test_debug_jenkins.py:17 time.sleep(1) KeyboardInterrupt`). The final report is `1 failed, 537 deselected, 1 error` + `Jenkins abort`, printed before Jenkins ends post. The lock was released after.
- **Cosmetic:**
  - a `Terminated` line after pytest's summary: the monitor, killed by the second abort's wave 2 while its `tail` waits for its next check of pytest's PID;
  - Jenkins's own `FlowInterruptedException` stack trace for an aborted post step (also in run 4).
