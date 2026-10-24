from __future__ import annotations

import pytest

import logging
import uuid
from dataclasses import asdict

from lib.tapdisk_test import (
    TD_RE_CONTROL,
    TD_RE_DISCONNECT_PENDING,
    parse_tdtest_events,
    tdtest_off,
    tdtest_send_config,
    tdtest_set,
)
from lib.windows.test_drivers import XENVBD_RE_DISABLE, XENVBD_RE_RESET
from tests.guest_tools.win.xenvbd_repro import (
    W_HOLD,
    W_RECOVER,
    W_SMOKE,
    W_SMOKE_LOG,
    Evidence,
    ReproContext,
    matching,
    tdtest_lba_window,
)

# Requirements: see tests/guest_tools/win/xenvbd_repro/README.md (test VM prerequisites are those of
# tests/guest_tools/win/test_nondestructive.py, plus --repro-* options). Smoke tests; must pass before the
# scenario tests are meaningful.

@pytest.mark.multi_vms
@pytest.mark.complex_prerequisites
@pytest.mark.usefixtures("windows_vm")
class TestReproSmoke:
    XENVBD_KNOBS: dict[str, int] = {}

    def test_s0_inert(self, repro: ReproContext) -> None:
        """An unaddressed tdtest tapdisk behaves as stock; a wrong vdi= is rejected; logging hits our minor only."""
        bs, log = repro.bs, repro.log
        bs.fill(1, W_SMOKE)
        load = bs.load(2, W_SMOKE, threads=4, io_size=256 * 1024, seconds=120)
        verify = bs.verify(W_SMOKE)
        # Taken before any teardown. The banner and `off ... reason=not-addressed` are control lines, not activity.
        tdtest_lines = [line for line in log.lines if "tdtest:" in line and not TD_RE_CONTROL.search(line)]
        repro.save_json("inert.json", {"load": load.raw, "verify_counts": verify.counts,
                                       "tdtest_lines": tdtest_lines})
        assert load.ok, f"load reported problems: {load.raw.get('mismatches')} {load.raw.get('errorCodes')}"
        assert verify.ok, f"verify reported problems: {verify.counts}"
        assert not tdtest_lines, f"unaddressed tdtest tapdisk logged: {tdtest_lines[:5]}"

        # A config naming another VDI must be rejected (guards against minor reuse).
        rejected = tdtest_send_config(repro.host, repro.disk.vdi.uuid, {"log": "1"}, conf_vdi=str(uuid.uuid4()))
        assert not rejected.accepted, f"config with a wrong vdi= was accepted: {rejected.line}"

        # Logging only: lines appear, and only for the test minor.
        tdtest_set(repro.host, repro.disk.vdi.uuid, {"log": 1, **tdtest_lba_window("log", W_SMOKE_LOG)})
        bs.load(3, W_SMOKE_LOG, threads=1, io_size=64 * 1024, seconds=15)
        events = parse_tdtest_events(log.lines)
        minors = {e.minor for e in events}
        repro.save_json("logging.json", {"events": len(events), "minors": sorted(m or -1 for m in minors)})
        assert events, "no tdtest data events logged with log=1"
        assert minors == {repro.disk.minor}, f"tdtest events for unexpected minors: {minors}"
        tdtest_off(repro.host, repro.disk.vdi.uuid)
        assert bs.verify(W_SMOKE).ok

    def test_s0_reset_observed(self, repro: ReproContext) -> None:
        """Hold one write longer than TimeOutValue: xenvbd resets and fakes, tapdisk keeps a dead ring."""
        bs, log = repro.bs, repro.log
        bs.fill(1, W_HOLD)
        bs.fill(1, W_RECOVER)
        repro.tdtest(write_hold_ms=20000, write_hold_max=1, **tdtest_lba_window("write_hold", W_HOLD),
                     log=1, **tdtest_lba_window("log", W_HOLD))
        held = bs.write_sync(2, W_HOLD)

        log.wait_for(XENVBD_RE_RESET, timeout=90, what="xenvbd TDTEST TargetReset")
        disables = log.wait_for(XENVBD_RE_DISABLE, timeout=60, what="xenvbd TDTEST BlkifRingDisable")
        disconnects = log.wait_for(TD_RE_DISCONNECT_PENDING, timeout=60,
                                   what="tapdisk disconnect from ring with N pending requests")
        faked = [int(m["faked"]) for m in disables]
        pending = [int(m["n"]) for m in disconnects]

        recover = bs.write_sync(2, W_RECOVER)
        recover_verify = bs.verify(W_RECOVER)
        ev = Evidence("S-0 reset observed", repro.variant.label, "n/a")
        ev.host = {
            "resets": [m.group(0) for _, m in matching(XENVBD_RE_RESET, log.lines)],
            "faked": faked,
            "disconnect_pending": pending,
        }
        ev.guest = {"held_write": held.raw, "recover_write": recover.raw, "recover_verify": recover_verify.counts}
        repro.save_json("evidence.json", asdict(ev))
        logging.info("reset observed: faked=%s pending=%s held write status=%s err=%s", faked, pending,
                     held.status, held.win32err)
        assert any(n > 0 for n in faked), f"BlkifRingDisable never faked a request: {faked}"
        assert any(n > 0 for n in pending), f"tapdisk never disconnected with pending requests: {pending}"
        assert repro.vm.is_ssh_up()
        assert recover.status == "OK" and recover_verify.ok, "the guest did not recover after the reset"
