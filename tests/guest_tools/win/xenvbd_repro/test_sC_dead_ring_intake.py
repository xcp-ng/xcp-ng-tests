from __future__ import annotations

import pytest

import time

from lib.tapdisk_test import TD_RE_DEAD_RING_DESTROY, TD_RE_GRANT_COPY_FAIL
from lib.windows.blockstamp import Window
from lib.windows.test_drivers import XENVBD_RE_RESET
from tests.guest_tools.win.xenvbd_repro import (
    W_SC1,
    W_SC2,
    W_SC3,
    Evidence,
    ReproContext,
    conclude,
    matching,
    problems_summary,
    tdtest_lba_window,
)

# S-C: dead-ring intake. Requests pushed to the ring but not yet consumed are taken off the dead ring after
# `Closed` (chkrng timer) and copied from grefs that were revoked or reissued.
# Requirements: see README.md in this directory.

HOLD_MS = 25000
HOLD_MS_GATED = 15000


@pytest.mark.multi_vms
@pytest.mark.complex_prerequisites
@pytest.mark.usefixtures("windows_vm")
class TestSCDeadRingIntake:
    XENVBD_KNOBS: dict[str, int] = {}

    def test_sC_dead_ring_intake(self, repro: ReproContext) -> None:
        bs, log = repro.bs, repro.log
        hold_ms = HOLD_MS_GATED if repro.variant.r3 else HOLD_MS

        for w in (W_SC1, W_SC2, W_SC3):
            bs.fill(1, w)
        # Hold one write on W1 so that the old ring becomes dead instead of being destroyed.
        repro.tdtest(write_hold_ms=hold_ms, write_hold_max=1, **tdtest_lba_window("write_hold", W_SC1),
                     log=1, **tdtest_lba_window("log", Window.span(W_SC1, W_SC2)))
        w1 = bs.start_write_sync(2, W_SC1)
        repro.wait_tdtest(lambda e: e.ev == "hold", timeout=60, what="ev=hold of the W1 write")
        # Stop intake on the rings live now (only the current ring); a ring connected later is unaffected.
        repro.tdtest(intake_pause="current")
        w2 = bs.start_write_sync(2, W_SC2)
        time.sleep(3)  # let xenvbd push the W2 requests onto the shared ring

        log.wait_for(XENVBD_RE_RESET, timeout=90, what="xenvbd TDTEST TargetReset")
        disconnect = repro.wait_tdtest(lambda e: e.ev == "disconnect", timeout=60, what="ev=disconnect")
        repro.wait_tdtest(lambda e: e.ev == "connect" and e.index > disconnect.index, timeout=90,
                          what="ev=connect of the new ring")
        res1 = bs.wait(w1)
        res2 = bs.wait(w2)
        # Reissue grefs through the new ring.
        load3 = bs.load(3, W_SC3, threads=2, io_size=64 * 1024, seconds=30)

        repro.tdtest(intake_pause=0, dead_chkrng_kick=1)
        repro.wait_tdtest(lambda e: e.ev == "release", timeout=hold_ms / 1000 + 60, what="ev=release of W1")
        try:
            log.wait_for(TD_RE_DEAD_RING_DESTROY, timeout=60, what="destroying dead ring")
        except TimeoutError:
            pass  # recorded below
        verifies = {name: bs.verify(w) for name, w in (("W1", W_SC1), ("W2", W_SC2), ("W3", W_SC3))}

        lines = log.lines
        events = repro.tdtest_events()
        dead_intake = [e for e in events if e.ev == "dead-intake" and e.index > disconnect.index]
        first_dead = min((e.index for e in dead_intake), default=len(lines))
        gcf = [m.group(0) for i, m in matching(TD_RE_GRANT_COPY_FAIL, lines) if i > first_dead]
        w2_bad = verifies["W2"].problems_in(W_SC2, ("MISDIRECTED", "CRC"))
        w3_bad = verifies["W3"].problems_in(W_SC3, ("MISDIRECTED", "CRC")) + [
            p for p in load3.problems if W_SC3.contains_block(p.lba)
        ]

        ev = Evidence("S-C", repro.variant.label, repro.options.expect)
        ev.host = {
            "disconnect": disconnect.line,
            "dead_intake": [e.line for e in dead_intake][:20],
            "grant_copy_failures_after_dead_intake": gcf[:20],
            "dead_ring_destroyed": [m.group(0) for _, m in matching(TD_RE_DEAD_RING_DESTROY, lines)],
            "gate": [e.line for e in events if e.ev in ("gate-open", "gate-close")][:10],
            "resets": len(matching(XENVBD_RE_RESET, lines)),
        }
        ev.guest = {
            "w1_write": {k: res1.raw.get(k) for k in ("status", "win32err", "durationMs")},
            "w2_write": {k: res2.raw.get(k) for k in ("status", "win32err", "durationMs")},
            "load3": {k: load3.raw.get(k) for k in ("reads", "writes", "readMismatches", "errorCodes")},
            "verify_counts": {name: v.counts for name, v in verifies.items()},
            "w2_problems": problems_summary(verifies["W2"], W_SC2, ("MISDIRECTED", "CRC")),
            "w3_problems": [{"lba": p.lba, "class": p.cls, "detail": p.detail, "source": p.source}
                            for p in w3_bad[:50]],
            # W1 shows S-B (STALE) as a side effect; recorded, not used for S-C.
            "w1_stale": len(verifies["W1"].problems_in(W_SC1, ("STALE",))),
        }
        if dead_intake:
            ev.notes.append("dead-ring intake with " + ("grant-copy failure" if gcf else "successful copy"))
        ev.host_evidence = bool(dead_intake)
        ev.guest_evidence = bool(w2_bad or w3_bad)
        ev.reproduced = ev.host_evidence
        conclude(repro, ev)
