from __future__ import annotations

import pytest

import logging

from lib.tapdisk_test import TD_RE_GRANT_COPY_FAIL, TdtestEvent
from lib.windows.test_drivers import XENVBD_RE_RESET, XENVBD_RE_UNKNOWN_ID, group_xenvbd_requests
from tests.guest_tools.win.xenvbd_repro import (
    DISK_TIMEOUT_VALUE_S,
    W_SA,
    W_SA_HOLD,
    Evidence,
    ReproContext,
    conclude,
    matching,
    problems_summary,
    tdtest_lba_window,
)

# S-A: abandoned read copied late. xenvbd fakes BLKIF_RSP_ERROR for a read tapdisk still holds and revokes
# its grants; tapdisk later grant-copies the read data to those grefs.
# Requirements: see README.md in this directory.

LOAD_SECONDS = 60
LEGACY_LOAD_SECONDS = 90


def _read_hold_ms(repro: ReproContext) -> int:
    # With an IOCTL reset the fake happens right away, so a short hold releases while xenvbd's close delay
    # keeps the backend Connected. With a TimeOutValue reset, the hold must outlast the timeout.
    return 3000 if repro.options.reset == "ioctl" else DISK_TIMEOUT_VALUE_S * 1000 + 2000


def _run_sa(repro: ReproContext, load_seconds: int) -> tuple[Evidence, list[TdtestEvent]]:
    bs, log = repro.bs, repro.log
    bs.fill(1, W_SA)
    repro.tdtest(read_hold_ms=_read_hold_ms(repro), read_hold_max=4, **tdtest_lba_window("read_hold", W_SA_HOLD),
                 log=1, **tdtest_lba_window("log", W_SA_HOLD))
    run = bs.start_load(2, W_SA, threads=2, io_size=64 * 1024, seconds=load_seconds, read_percent=70)
    repro.wait_tdtest(lambda e: e.ev == "hold", timeout=60, what="ev=hold (a read is held)")
    if repro.options.reset == "ioctl":
        reset = bs.reset_device()
        logging.info("IOCTL_STORAGE_RESET_DEVICE: ok=%s err=%s", reset.ok, reset.win32err)
    log.wait_for(XENVBD_RE_RESET, timeout=90, what="xenvbd TDTEST TargetReset")
    load = bs.wait(run)
    repro.wait_tdtest(lambda e: e.ev == "release", timeout=60, what="ev=release of the held read")
    verify = bs.verify(W_SA)

    lines = log.lines
    tdevents = repro.tdtest_events()
    releases = [e for e in tdevents if e.ev == "release" and e.dead is False]
    # xenvbd fakes (event token exactly `fake`) with their grefs, from the `fake-grefs` lines. Both ids are
    # ints here: xenvbd's are hex in the log, tapdisk's decimal.
    fakes = [r for r in group_xenvbd_requests(repro.xenvbd_events()) if r.event == "fake"]
    gcf = matching(TD_RE_GRANT_COPY_FAIL, lines)

    correlated = []
    for f in fakes:
        fgrefs = set(f.grefs)
        for r in releases:
            if r.index < f.index:
                continue
            if not ((f.id is not None and r.id == f.id) or bool(fgrefs & set(r.grefs))):
                continue
            copy_fail = [gm.group(0) for gi, gm in gcf if gi > r.index
                         and (r.id is None or int(gm["rid"] or gm["sid"] or -1) in (r.id, -1))]
            correlated.append({"fake": lines[f.index], "release": r.line, "grant_copy_failures": copy_fail})

    mismatches = [p for p in load.problems if W_SA.contains_block(p.lba)]
    verify_bad = verify.problems_in(W_SA, ("MISDIRECTED", "CRC"))
    ev = Evidence("S-A", repro.variant.label, repro.options.expect)
    ev.host = {
        "fakes": [lines[f.index] for f in fakes][:20],
        "releases_dead0": [r.line for r in releases][:20],
        "grant_copy_failures": [m.group(0) for _, m in gcf][:20],
        "correlated": correlated,
        "unknown_id": [m.group(0) for _, m in matching(XENVBD_RE_UNKNOWN_ID, lines)][:20],
        "resets": len(matching(XENVBD_RE_RESET, lines)),
    }
    ev.guest = {
        "load": {k: load.raw.get(k) for k in ("reads", "writes", "readMismatches", "errorCodes", "ok")},
        "load_mismatches": [{"lba": p.lba, "class": p.cls, "detail": p.detail} for p in mismatches[:50]],
        "verify_counts": verify.counts,
        "verify_problems": problems_summary(verify, W_SA, ("MISDIRECTED", "CRC", "STALE")),
    }
    ev.guest_evidence = bool(mismatches or verify_bad)
    return ev, releases


@pytest.mark.multi_vms
@pytest.mark.complex_prerequisites
@pytest.mark.usefixtures("windows_vm")
class TestSAAbandonedRead:
    # Keep the backend Connected for up to 5 s after the fake, so that the late copy happens before
    # tapdisk disconnects (guest-side widening of the S-A window).
    XENVBD_KNOBS: dict[str, int] = {"test-close-delay-ms": 5000}

    def test_sA_abandoned_read(self, repro: ReproContext) -> None:
        if repro.variant.legacy:
            pytest.skip("legacy drivers: see test_sA_legacy_positive_control")
        ev, releases = _run_sa(repro, LOAD_SECONDS)
        # D0: a fake (id/grefs G) earlier than a dead=0 release of the same request followed by a grant-copy
        # error. If G was reissued instead, the evidence is guest-side, with the release seen on the host.
        with_copy_error = [c for c in ev.host["correlated"] if c["grant_copy_failures"]]
        ev.host_evidence = bool(with_copy_error) or bool(ev.host["grant_copy_failures"])
        ev.reproduced = bool(with_copy_error) or (ev.guest_evidence and bool(releases))
        if with_copy_error:
            ev.notes.append("late grant-copy to revoked grefs (EPERM path)")
        elif ev.guest_evidence:
            ev.notes.append("grefs reissued: guest-side corruption")
        conclude(repro, ev)


@pytest.mark.multi_vms
@pytest.mark.complex_prerequisites
@pytest.mark.usefixtures("windows_vm")
class TestSALegacyPositiveControl:
    XENVBD_KNOBS: dict[str, int] = {}

    def test_sA_legacy_positive_control(self, repro: ReproContext) -> None:
        if not repro.variant.legacy:
            pytest.skip("needs the D0-legacy drivers (--repro-variant=D0-legacy)")
        ev, releases = _run_sa(repro, LEGACY_LOAD_SECONDS)
        ev.host_evidence = bool(releases) or bool(ev.host["unknown_id"])
        ev.reproduced = ev.guest_evidence and ev.host_evidence
        conclude(repro, ev)
