from __future__ import annotations

import pytest

import logging
import time

from lib.commands import SSHCommandFailed
from lib.common import wait_for
from lib.windows.test_drivers import start_vm_on_host
from lib.windows.test_drivers import XENVBD_RE_RESET, XenvbdEvent
from tests.guest_tools.win.xenvbd_repro import W_SA_HOLD, Evidence, ReproContext, conclude, tdtest_lba_window

# S-D (optional, observation only, test VM only): shut the guest down while tapdisk holds a read and
# record what happens (clean shutdown, bugcheck 0x9F/0x133, ...). Needs --repro-run-shutdown.
# Requirements: see README.md in this directory.

HOLD_MS = 20000       # default for --repro-sd-hold-ms
MIN_HOLD_MS, MAX_HOLD_MS = 1000, 120000  # tapdisk tdtest caps hold at 120 s
MAX_SD_RESETS = 10
RESET_GAP_S = 1.5
# With --repro-reset=timeout (the default) the resets are StorPort timeouts, ~10 s apart; --repro-reset=ioctl uses
# IOCTL_STORAGE_RESET_DEVICE, which failed with ERROR_INVALID_FUNCTION on the test VM.


def gref_overlaps(events: list[XenvbdEvent]) -> dict[str, object]:
    """Grefs seen in more than one ring generation (generations start at each ring-connect of the target)."""
    gens: list[set[int]] = []
    ring_gens: list[set[int]] = []
    ids: list[int] = []
    for e in events:
        if e.event == "ring-connect":
            gens.append(set())
            ring_gens.append(set(e.grefs))
            ids.append(e.index)
        if e.event == "ring-connect-grefs" and ring_gens:
            ring_gens[-1].update(e.grefs)
        if e.event.endswith("-grefs") and e.event != "ring-connect-grefs" and gens:
            gens[-1].update(e.grefs)
    for rg, g in zip(ring_gens, gens):
        g |= rg
    overlaps = []
    for i in range(len(gens)):
        for j in range(i + 1, len(gens)):
            common = sorted(gens[i] & gens[j])
            if common:
                overlaps.append({"gens": [i, j], "count": len(common), "grefs": common[:16]})
    return {"generations": len(gens), "per_gen_grefs": [len(g) for g in gens], "overlaps": overlaps}


@pytest.mark.multi_vms
@pytest.mark.complex_prerequisites
@pytest.mark.usefixtures("windows_vm")
class TestSDShutdownWithHeldRead:
    XENVBD_KNOBS: dict[str, int] = {}

    def test_sD_shutdown_with_held_read(self, repro: ReproContext) -> None:
        bs, vm = repro.bs, repro.vm
        bs.fill(1, W_SA_HOLD)
        n_resets = repro.options.sd_resets
        assert 0 <= n_resets <= MAX_SD_RESETS, f"--repro-sd-resets must be 0..{MAX_SD_RESETS}"
        hold_ms = repro.options.sd_hold_ms
        assert MIN_HOLD_MS <= hold_ms <= MAX_HOLD_MS, f"--repro-sd-hold-ms must be {MIN_HOLD_MS}..{MAX_HOLD_MS}"
        repro.tdtest(read_hold_ms=hold_ms, read_hold_max=2 + n_resets, **tdtest_lba_window("read_hold", W_SA_HOLD),
                     log=1, **tdtest_lba_window("log", W_SA_HOLD))
        load_seconds = min(300, 60 + 15 * n_resets + hold_ms // 1000)
        bs.start_load(2, W_SA_HOLD, threads=1, io_size=16 * 1024, seconds=load_seconds, read_percent=100)
        repro.wait_tdtest(lambda e: e.ev == "hold", timeout=60, what="ev=hold (a read is held)")

        resets_ok = []
        for i in range(n_resets):
            if repro.options.reset == "ioctl":
                r = bs.reset_device()
                logging.info("S-D reset %d/%d: ok=%s err=%s", i + 1, n_resets, r.ok, r.win32err)
                resets_ok.append(bool(r.ok))
                time.sleep(RESET_GAP_S)
            else:
                # TimeOutValue resets: each one is StorPort giving up on the held read, ~10 s after it was held.
                repro.log.wait_for(XENVBD_RE_RESET, timeout=90, count=i + 1, what=f"xenvbd TargetReset #{i + 1}")
                logging.info("S-D reset %d/%d seen", i + 1, n_resets)
                resets_ok.append(True)

        domid = vm.param_get("dom-id")
        t0 = time.monotonic()
        try:
            vm.ssh("shutdown.exe -s -f -t 0")
        except SSHCommandFailed as e:
            logging.info("shutdown command returned %s", e.returncode)

        def settled() -> bool:
            return vm.is_halted() or (vm.is_running() and vm.param_get("dom-id") not in (domid, "-1"))

        wait_for(settled, "Wait for the guest to halt (or to restart after a crash)", timeout_secs=300,
                 retry_delay_secs=5)
        elapsed = time.monotonic() - t0
        state = vm.power_state()
        restarted = state == "running"
        log_lines = repro.log.lines

        if not vm.is_running():
            start_vm_on_host(vm, repro.host)
        else:
            wait_for(vm.is_ssh_up, "Wait for SSH up", timeout_secs=600)
        crash = vm.execute_powershell_script(
            r"""$d = Test-Path "$env:SystemRoot\MEMORY.DMP";
$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=1001} -MaxEvents 3 -ErrorAction SilentlyContinue |
    Where-Object { $_.TimeCreated -gt (Get-Date).AddMinutes(-30) } | ForEach-Object { $_.Message };
"dump=$d"; $e"""
        )

        ev = Evidence("S-D", repro.variant.label, "observe")
        ev.host = {
            "tdtest": [line for line in log_lines if "tdtest:" in line][-30:],
            "xenvbd": [line for line in log_lines if "TDTEST" in line][-30:],
        }
        ev.host["gref_overlap"] = gref_overlaps(repro.xenvbd_events())
        ev.guest = {
            "hold_ms": hold_ms,
            "resets_requested": n_resets,
            "resets_ok": resets_ok,
            "seconds_to_settle": round(elapsed, 1),
            "restarted_by_itself": restarted,
            "crash_info": crash,
        }
        ev.notes.append("observation only")
        conclude(repro, ev)
