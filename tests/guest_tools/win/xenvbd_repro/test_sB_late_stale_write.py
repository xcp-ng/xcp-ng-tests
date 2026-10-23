from __future__ import annotations

import pytest

from lib.tapdisk_test import TD_RE_DEAD_RING_DESTROY
from lib.windows.test_drivers import XENVBD_RE_RESET
from tests.guest_tools.win.xenvbd_repro import (
    W_SB,
    Evidence,
    ReproContext,
    conclude,
    matching,
    problems_summary,
    tdtest_lba_window,
)

# S-B: late stale write. A write held on a dead tapdisk ring lands after newer data written through the
# new ring (lost update). Requirements: see README.md in this directory.

# R3' (gate new rings while a dead ring has pending writes) stalls the guest for the whole hold, so the
# hold is kept at 15 s with it.
HOLD_MS = 25000
HOLD_MS_GATED = 15000


@pytest.mark.multi_vms
@pytest.mark.complex_prerequisites
@pytest.mark.usefixtures("windows_vm")
class TestSBLateStaleWrite:
    XENVBD_KNOBS: dict[str, int] = {}

    def test_sB_late_stale_write(self, repro: ReproContext) -> None:
        bs, log = repro.bs, repro.log
        hold_ms = HOLD_MS_GATED if repro.variant.r3 else HOLD_MS

        bs.fill(1, W_SB)
        repro.tdtest(write_hold_ms=hold_ms, write_hold_max=1, **tdtest_lba_window("write_hold", W_SB),
                     log=1, **tdtest_lba_window("log", W_SB))
        # Times out, then reset, fake, and a classpnp retry (through the new ring) or a failure.
        gen2 = bs.write_sync(2, W_SB)
        disconnect = repro.wait_tdtest(lambda e: e.ev == "disconnect", timeout=60, what="ev=disconnect")
        repro.wait_tdtest(lambda e: e.ev == "connect" and e.index > disconnect.index, timeout=90,
                          what="ev=connect of the new ring")
        gen3 = bs.write_sync(3, W_SB)
        assert gen3.status == "OK", f"the gen 3 write was not acknowledged: {gen3.raw}"

        release = repro.wait_tdtest(lambda e: e.ev == "release" and e.dead is True,
                                    timeout=hold_ms / 1000 + 60, what="ev=release dead=1")
        log.wait_for(TD_RE_DEAD_RING_DESTROY, timeout=60, what="destroying dead ring")
        verify = bs.verify(W_SB)

        events = repro.tdtest_events()
        new_ring_completes = [
            e for e in events
            if e.ev == "complete" and e.index < release.index and e.ring != release.ring
            and e.sector is not None and W_SB.contains_sector(e.sector)
        ]
        gen2_crcs = set(gen2.block_crcs)
        crc_match = bool(gen2_crcs & set(release.crcs)) if release.crcs else None
        stale = verify.problems_in(W_SB, ("STALE",))
        stale_gen2 = [p for p in stale if p.hdr_gen == 2]

        ev = Evidence("S-B", repro.variant.label, repro.options.expect)
        ev.host = {
            "release": release.line,
            "release_crc_matches_gen2": crc_match,
            "new_ring_completes_before_release": [e.line for e in new_ring_completes][:10],
            "resets": len(matching(XENVBD_RE_RESET, log.lines)),
            "dead_ring_destroyed": [m.group(0) for _, m in matching(TD_RE_DEAD_RING_DESTROY, log.lines)],
        }
        ev.guest = {
            "gen2_write": {k: gen2.raw.get(k) for k in ("status", "win32err", "durationMs", "seq")},
            "gen3_write": {k: gen3.raw.get(k) for k in ("status", "win32err", "durationMs", "seq")},
            "verify_counts": verify.counts,
            "verify_problems": problems_summary(verify, W_SB, ("STALE", "MISDIRECTED", "CRC", "UNWRITTEN")),
        }
        if repro.variant.r3:
            ev.notes.append(f"R3' guest stall: gen3 write took {gen3.raw.get('durationMs')} ms, "
                            f"{ev.host['resets']} reset(s)")
        if crc_match is None:
            ev.notes.append("tdtest release line carries no crc=; CRC correlation skipped")
        ev.guest_evidence = bool(stale)
        ev.host_evidence = bool(new_ring_completes) and crc_match is not False
        ev.reproduced = bool(stale_gen2) and ev.host_evidence
        conclude(repro, ev)
