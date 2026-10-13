# Jenkins probe (PLAN.md, phase 1)

Temporary diagnostics, plus the planned abort behavior (see the `jenkins_probe.py` docstring):

- the first SIGTERM/SIGHUP redirects the output to `pytest.log`, then continues pdb or interrupts the test (unless in a teardown);
- later SIGTERM/SIGHUP are ignored;
- SIGINT stays a `KeyboardInterrupt`.

- `jenkins_probe.py`: pytest plugin (`-p jenkins_probe`, needs `investigation/probe` in `PYTHONPATH`). It writes to `$WORKSPACE/.jenkins-pytest/probe.log`:
  - startup context: process tree, sessions, fds, Jenkins env vars;
  - every signal with the current phase and pdb state;
  - ancestor processes dying (`screen`, `teetty`, …);
  - after the first signal, a heartbeat every 2s with CPU usage and whether writes to the original stdout/stderr still work.
- `screen_run.sh`: minimal DEBUG launcher (`screen` + `teetty`, no `continue` injection, no screen quit).
- `post_probe.sh`: post/always step. It prints the probe log and follows it while pytest is alive (max 240s), then cleans up with SIGINT, 3s, SIGKILL. Finally it prints `pytest.log` and the status.
- `flaky-tests.probe`: probe version of the job.

## Running it

1. Commit `investigation/` to an xcp-ng-tests branch and use it as `BRANCH`.
   The job checks out the repo into `$WORKSPACE` and uses the scripts from there.
2. Put `flaky-tests.probe` in a ci-xcp-ng branch, in place of `flaky-tests`, and point `CI_BRANCH` at it.
3. Run with `PYTEST_PARAMS` containing `-k jenkins_debug`, and abort by hand:
   - **DEBUG=false**, aborted during the 60s test sleep
   - **DEBUG=true**, aborted while in pdb (after ~60s, once `ipdb>` shows up)
   - optionally, either one again, plus a second abort while `post` is running
4. Send me the full console output. Everything from `probe.log` is printed in `post`.
