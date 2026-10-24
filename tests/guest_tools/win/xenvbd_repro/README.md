# xenvbd/tapdisk shutdown-ordering repro

Reproduces, and checks remediations for, the hazards described in win-pvdrivers
`docs/xenvbd-shutdown-ordering.md` and `docs/tapdisk-dead-ring-late-writes.md`:

| Test | Hazard |
|---|---|
| `test_s0_inert`, `test_s0_reset_observed` | Smoke tests. Run them first. |
| `test_sA_abandoned_read` / `test_sA_legacy_positive_control` | S-A: a read is copied late, after xenvbd faked its completion and revoked its grants |
| `test_sB_late_stale_write` | S-B: a write held on a dead tapdisk ring lands after newer data |
| `test_sC_dead_ring_intake` | S-C: requests left on the dead ring are taken after `Closed` |
| `test_sD_shutdown_with_held_read` | S-D (optional, observation only, needs `--repro-run-shutdown`) |

## Safety on a shared host

- Only the tapdisk that serves the test VDI is configured or signalled. Each time, its PID comes fresh from a
  `tap-ctl list` row whose path contains the VDI UUID, and exactly one row must match. The PID is checked again
  on the host right before the signal, and real-time signals only go to a PID that runs the tdtest binary.
  Nothing uses `killall`, `pkill` or a broadcast. tapback is never touched.
- `/usr/libexec/tapdisk` is replaced by the tdtest build only during the few seconds of each test VBD
  plug (the "swap window"). The stock binary is put back with `cp -p` from `/root/tdtest/tapdisk.stock`, and
  its sha256 is checked after every window. Each window is marked in `daemon.log` with
  `logger -t xcpng-repro`.
- Before the first swap, the tests record `rpm -q blktap`, `rpm -V blktap`, the tapdisk sha256, the XCP-ng
  version and `uname -r` (in `/root/tdtest/baseline-*.json` and `<artifacts>/tdtest-deployment.json`). They stop
  if `/usr/libexec/tapdisk` does not match the RPM and is not the recorded stock binary.
- The tests write files under `/root/tdtest/` and `/run/tapdisk-test/` on the host. Confirm this once
  before the first run.
- Load is bounded: at most 4 threads, 1 MiB per I/O and 300 s per run. These limits are enforced in Python and
  again in `blockstamp.ps1`.
- The test VM is never migrated: every start uses `on=<test host>`, and its residence is checked after every
  boot. A pre-test snapshot is taken, then reverted and destroyed at the end of each test module. The VM is left
  halted.
- Test VDIs (2 GiB, label `xenvbd-repro`) are created only on `--repro-sr`.
- `blockstamp.ps1` only writes to a disk that is RAW, has no partitions, is not the system or boot disk, does
  not hold `C:`, and is exactly 2 GiB. It never writes the first 1 MiB of the disk.

Rollback, if a stock binary was ever left out of place: `cp -p /root/tdtest/tapdisk.stock /usr/libexec/tapdisk`
or `yum reinstall blktap` (ask first).

## Prerequisites

- The Windows test VM (`--vm=<uuid>`) meets the requirements in the header of
  `tests/guest_tools/win/test_nondestructive.py`: no PV tools, it reports its IP on the serial console, OpenSSH
  with Git Bash as `DefaultShell`, and `root` is an administrator. It resides on the `--hosts` host (or is halted;
  in that case it is started there).
- `--repro-drivers`: a zip of one staged build (`<drv>\x64\<drv>.{inf,sys,cat}` for xenbus, xeniface,
  xenvbd, xenvif and xennet, plus `testsign.crt`), for example `repro-out\D0-hooks.zip`. It is installed with the
  unchanged `scripts/guests/windows/install-drivers.ps1`.
- `--repro-tapdisk` and `--repro-tdtest-ctl`: the tdtest `tapdisk` and `tdtest-ctl` built in the XCP-ng build
  container from the source that matches the host's blktap RPM.
- `data.py` must import on the test runner. On Windows, a `data.py` that still does `import legacycrypt` fails
  with `libcrypt / libxcrypt missing`. Use `lib.common.hash_password`, as `data.py-dist` does.

## Commands

The examples use the Phase 0 environment: host `192.168.1.60` (a single-host XCP-ng 8.3 pool), VM
`ce56dc80-2273-f6ac-2680-e7cf26a8d25c` and the local ext SR `f6a40dd7-0494-3ae3-be24-9fb84bcaf863`. Run them
from the root of xcp-ng-tests.

Smoke tests (they must pass first):

```
uv run pytest tests/guest_tools/win/xenvbd_repro -k s0 --hosts=192.168.1.60 --vm=ce56dc80-2273-f6ac-2680-e7cf26a8d25c --repro-sr=f6a40dd7-0494-3ae3-be24-9fb84bcaf863 --repro-drivers=E:\code\win-pvdrivers\repro-out\D0-hooks.zip --repro-tapdisk=<path>\tapdisk --repro-tdtest-ctl=<path>\tdtest-ctl
```

Phase 4 scenarios on the current code (D0 hooks build, no fixes):

```
uv run pytest tests/guest_tools/win/xenvbd_repro -k "sA or sB or sC" --repro-variant=D0 --repro-expect=reproduce --hosts=192.168.1.60 --vm=ce56dc80-2273-f6ac-2680-e7cf26a8d25c --repro-sr=f6a40dd7-0494-3ae3-be24-9fb84bcaf863 --repro-drivers=E:\code\win-pvdrivers\repro-out\D0-hooks.zip --repro-tapdisk=<path>\tapdisk --repro-tdtest-ctl=<path>\tdtest-ctl
```

S-A positive control with the legacy build:

```
uv run pytest tests/guest_tools/win/xenvbd_repro -k sA --repro-variant=D0-legacy --repro-expect=reproduce --repro-drivers=E:\code\win-pvdrivers\repro-out\D0-legacy.zip <other options as above>
```

Phase 5 matrix cells: `--repro-variant` names the build and the remediations. `R1` sets the xenvbd registry
knob `test-defer-fake=1`, `R2` sets the tdtest key `fix_dead_intake=1`, and `R3` (R3') sets
`fix_gate_new_ring=1`. Use `--repro-expect=clean` wherever the fix should work, for example:

```
uv run pytest tests/guest_tools/win/xenvbd_repro -k "sB or sC" --repro-variant=D0+R2+R3 --repro-expect=clean <other options as above>
uv run pytest tests/guest_tools/win/xenvbd_repro -k sA --repro-variant=D0+R1 --repro-expect=clean <other options as above>
```

`--repro-expect=observe` records evidence without asserting. This is useful while tuning timing.
`--repro-reset=ioctl` makes S-A trigger the reset with `IOCTL_STORAGE_RESET_DEVICE` and a 3 s read hold, instead
of waiting for the disk `TimeOutValue` (10 s) with a 12 s hold. The optional S-D test needs
`--repro-run-shutdown`.

Through the job runner: `uv run ./jobs.py run windows-xenvbd-repro 192.168.1.60 --vm=<uuid> --repro-sr=...`
(with the same `--repro-*` options).

## Output

Output goes to `--repro-artifacts` (default `repro-artifacts/`), with one timestamped directory per session:
- `tdtest-deployment.json`: the host baseline, hashes and swap-window timestamps.
- `boot-*.host.log`: the log slice around each knob reboot. The xenvbd `TDTEST` banner proves which build ran.
- one directory per test, containing:
  - `*.host.log`: the `daemon.log` slice. It contains only lines from `tapdisk[<test pid>]`,
    `qemu-dm-<test domid>[` and `xcpng-repro`.
  - `guest/`: blockstamp JSON results and the ledger.
  - `evidence.json`: what was found and the conclusion.

Every matrix cell needs at least 3 runs.

## Adjusting to the real log formats

Every log pattern lives in one place:
- tapdisk, tdtest and host markers: `lib/tapdisk_test.py` (the `TD_RE_*`, `TD_KV_RE`, `*_ERE` and `TD_*_BASE`
  constants, `TDTEST_KEYS`, `TDTEST_LBA_END_INCLUSIVE`; tdtest windows are half-open, xenvbd's are inclusive);
- xenvbd: `lib/windows/test_drivers.py` (the `XENVBD_RE_*`, `XENVBD_KV_RE` and `XENVBD_*_BASE` constants).

The disk layout (4 KiB blocks; block N is sectors 8N..8N+7) is in `__init__.py` (`W_*`, `XENVBD_LOG_WINDOW`).

Parsing rules that matter when the formats change:
- tdtest ids are decimal, xenvbd ids are hex; both are converted to int before they are correlated.
- A xenvbd request line (`fake`, `submit`, `rsp`, ...) carries no grefs. They are on following `<event>-grefs` lines
  with the same target, ring and id; `group_xenvbd_requests()` joins them. Event tokens match exactly (`fake` is
  not `fake-grefs` or `deferred-fake`).
- Every tdtest reload is a full spec: `tdtest_set()` rewrites all keys with `vdi=` and a fresh `seq`, drops one-shot
  keys (`dead_chkrng_kick`), waits for `tdtest: config minor=<m> vdi=... seq=<seq> ` and fails on
  `config rejected`. Accepted reloads reset the `*_hold_max` counters.
- xenvbd knobs only act on targets in `test-targets`. The target of the test disk is its userdevice (the xvd device
  id is `(202 << 8) | (userdevice << 4)`, which xenvbd maps back with `__ParseVbd()`);
  `configure_repro_registry(..., target=)` writes `test-targets = 1 << target`, and `repro_vdi` checks the xenstore
  backend device id and the `target=` of the `ring-connect` lines after each plug.
- S-C reports `gref reissued to a new ring page` as its own class (`classify_dead_intake()`): a tdtest
  `dead-intake` whose grefs are among those of a later xenvbd `ring-connect`.
