from __future__ import annotations

import pytest

import logging
import time

from lib.commands import SSHCommandFailed
from lib.common import wait_for
from lib.windows.test_drivers import start_vm_on_host
from tests.guest_tools.win.xenvbd_repro import W_SA_HOLD, Evidence, ReproContext, conclude, tdtest_lba_window

# S-D (optional, observation only, test VM only): shut the guest down while tapdisk holds a read and
# record what happens (clean shutdown, bugcheck 0x9F/0x133, ...). Needs --repro-run-shutdown.
# Requirements: see README.md in this directory.

HOLD_MS = 20000


@pytest.mark.multi_vms
@pytest.mark.complex_prerequisites
@pytest.mark.usefixtures("windows_vm")
class TestSDShutdownWithHeldRead:
    XENVBD_KNOBS: dict[str, int] = {}

    def test_sD_shutdown_with_held_read(self, repro: ReproContext) -> None:
        bs, vm = repro.bs, repro.vm
        bs.fill(1, W_SA_HOLD)
        repro.tdtest(read_hold_ms=HOLD_MS, read_hold_max=2, **tdtest_lba_window("read_hold", W_SA_HOLD),
                     log=1, **tdtest_lba_window("log", W_SA_HOLD))
        bs.start_load(2, W_SA_HOLD, threads=1, io_size=16 * 1024, seconds=60, read_percent=100)
        repro.wait_tdtest(lambda e: e.ev == "hold", timeout=60, what="ev=hold (a read is held)")

        domid = vm.param_get("dom-id")
        t0 = time.monotonic()
        try:
            vm.ssh("shutdown.exe -s -f -t 0")
        except SSHCommandFailed as e:
            logging.info("shutdown command returned %s", e.returncode)

        def settled() -> bool:
            return vm.is_halted() or (vm.is_running() and vm.param_get("dom-id") not in (domid, "-1"))

        wait_for(settled, "Wait for the guest to halt (or to restart after a crash)", timeout_secs=600,
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
        ev.guest = {"seconds_to_settle": round(elapsed, 1), "restarted_by_itself": restarted, "crash_info": crash}
        ev.notes.append("observation only")
        conclude(repro, ev)
