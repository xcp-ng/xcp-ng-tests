The goal here is to perform an investigation on how to cleanly manage job cancellation in jenkins.

The main idea here is that pytest teardown might take more ~1 minute in some cases, more than the jenkins timeout.

The solution should be to update pytest (through `conftest.py`) to detect when we are in a jenkins env, and implement specific logic:
- for signal handling
- for exposing the logs through a dedicated file on disk

This way, the task can terminate while letting the pytest process run in a background, and a post/always jenkins task can pick it up and monitor the teardown.

There are files here that corresponds to the current configuration for a flaky job.

This job will run on jenkins with `-k jenkins_debug` as a way to validate the approach.

Several parameters:
- DEBUG might be true or false (see the `scripts/detash.sh` for the implications)
- the jenkins abort might happen before the exception is raised, or after while in a pdb in a screen


More things:
- the post/always task should have a way to terminate the tests on a second abort
- the jenkins killing process is quite tricky
- the `detash.sh` logic is quite tricky, feel free to re-write a simpler version (in python if necessary)
- the `detash.sh` has a weird "send 'continue' to stdin and quit the screen after 1 second behavior" which can be removed completely
- DEBUG is tricky because many processes are involved: `detash.sh`, `screen`, `teetty`, `pytest`.
- the change to the existing jenkins script should be minimal, so the cleanup task logic should go to a specific script in `./script`
- you probably want to add logs for the first test, to confirm your assumptions (it's a hard task)


## Decisions

These were clarified after the initial instructions.

- **Abort while in pdb (DEBUG=true)**: leave pdb automatically and continue to the teardown, as `DETASH_ON_QUIT=continue` used to.
  The post/always task streams the teardown logs.
- **Second abort**: interrupt the teardown, the way a SIGINT would, so pytest exits soon even if the host stays dirty.
  - Jenkins may send SIGTERM to pytest **several times** during the first abort.
    pytest has to tolerate those repeated SIGTERMs, so a repeated SIGTERM must not be read as a "second abort".
  - Use **SIGINT** for the second abort: Jenkins does not send it, so it cleanly tells the two cases apart.
    The post/always task sends SIGINT to pytest when it is itself aborted.
- **Post/always timeout**: fixed, around 15 minutes (adjustable). A single one: the Jenkins `timeout` around the post step, which acts as a second abort.
  When it expires, interrupt the teardown the same way as a second abort (SIGINT).
- **Host lock**: move `lock(resource:null, label: params.HOST_A1)` to the stage `options` so it also covers the post section.
  The next job must not get the host while the teardown is still running.
- **Jenkins detection**: in `conftest.py`, check environment variables Jenkins always sets (e.g. `JENKINS_URL`, `BUILD_TAG`).
- **Build status**: ABORTED by default.
  Marking the build FAILURE when the teardown itself errored would be nice, but only if it stays simple.
- **Deliverables**:
  - pytest-side logic goes in `conftest.py`.
  - The cleanup/monitoring logic goes in a script under `./scripts`.
  - The modified Jenkins files go next to the originals in `investigation/`, as `debug_env.groovy` and `flaky-tests`.
    They are ported to the ci-xcp-ng repo by hand, so keep the changes minimal.
- **Previous attempt**: its files were removed on purpose. Start from the `*.original` files in this directory.

## Assumptions to verify in the first run

Log enough to confirm these, since none of them is proven yet:

- the signal Jenkins sends on abort, how many times, which processes receive it (process group? `screen`/`teetty` children in DEBUG mode?), and whether SIGKILL follows
- how long Jenkins waits before it stops following the `sh` step and runs `post`
- whether the pytest process (and `screen`, in DEBUG mode) survives the abort of the `sh` step
