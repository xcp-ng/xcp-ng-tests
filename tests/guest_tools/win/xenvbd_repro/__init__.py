"""
Shared layout, context and evidence helpers for the xenvbd/tapdisk shutdown-ordering repro tests.

Scenarios (see docs/ring-faults/ in win-pvdrivers):
- S-A: abandoned read copied late by tapdisk after xenvbd faked its completion and revoked its grants.
- S-B: late stale write: a write held on a dead tapdisk ring lands after newer data written on the new ring.
- S-C: dead-ring intake: requests left unconsumed on the dead ring are taken after `Closed`.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lib.host import Host
from lib.snapshot import Snapshot
from lib.tapdisk_test import (
    LogSlice,
    TdtestConfigResult,
    TdtestDeployment,
    TdtestEvent,
    parse_tdtest_events,
    tdtest_set,
)
from lib.vbd import VBD
from lib.vdi import VDI
from lib.vm import VM
from lib.windows.blockstamp import Blockstamp, BlockstampResult, Window
from lib.windows.test_drivers import (
    XENVBD_RE_BANNER,
    XenvbdEvent,
    group_xenvbd_requests,
    parse_xenvbd_events,
    xenvbd_ring_connects,
)

from typing import Any, Callable

REPRO_DISK_SIZE = 2 * 1024**3

# --------------------------------------------------------------------------------------------------
# Disk layout, in 4 KiB blocks. The first group is inside the xenvbd submission/response log window;
# the load windows are kept outside it so that bulk I/O never floods the shared host's log.
# --------------------------------------------------------------------------------------------------

W_HOLD = Window(4096, 8)        # S-0 reset-observed: one held write
W_SB = Window(4160, 8)          # S-B: the held, then overwritten, write (8 segments, one blkif request)
W_SC1 = Window(4224, 8)         # S-C: held write that keeps the old ring dead
W_SC2 = Window(4288, 8)         # S-C: writes left unconsumed on the old ring
W_RECOVER = Window(4352, 8)     # post-reset recovery check
XENVBD_LOG_WINDOW = Window.span(W_HOLD, W_SB, W_SC1, W_SC2, W_RECOVER)

W_SMOKE = Window(16384, 2048)   # S-0 inert: 8 MiB load window
W_SMOKE_LOG = Window(16384, 64) # S-0: tdtest logging-only check
W_SA = Window(24576, 256)       # S-A: 1 MiB load window
W_SA_HOLD = Window(24576, 16)   # S-A: reads held (and logged by tdtest) only in this sub-window
W_SC3 = Window(32768, 1024)     # S-C: load that makes xenvbd reissue grefs

DISK_TIMEOUT_VALUE_S = 10

EXPECT_CHOICES = ("reproduce", "clean", "observe")
RESET_CHOICES = ("timeout", "ioctl")

# Build label (from --repro-variant) -> variant string expected in the xenvbd TDTEST banner.
BANNER_FOR_BUILD = {"D0": "D0-hooks"}


@dataclass(frozen=True)
class Variant:
    """--repro-variant: `<build>[+R1][+R2][+R3]`, e.g. D0, D0-legacy, D0+R1, D0+R2+R3, D2-r4."""

    label: str
    build: str
    r1: bool = False
    r2: bool = False
    r3: bool = False

    @classmethod
    def parse(cls, label: str) -> Variant:
        tokens = [t.strip() for t in label.split("+") if t.strip()]
        if not tokens:
            raise ValueError("empty variant label")
        fixes = {t.upper().rstrip("'") for t in tokens[1:]}
        unknown = fixes - {"R1", "R2", "R3"}
        if unknown:
            raise ValueError(f"unknown remediation(s) {sorted(unknown)} in variant {label!r}")
        return cls(label, tokens[0], "R1" in fixes, "R2" in fixes, "R3" in fixes)

    @property
    def legacy(self) -> bool:
        return "legacy" in self.build.lower()

    @property
    def expected_banner(self) -> str:
        return BANNER_FOR_BUILD.get(self.build, self.build)

    def tdtest_fixes(self) -> dict[str, int]:
        return {"fix_dead_intake": int(self.r2), "fix_gate_new_ring": int(self.r3)}

    def xenvbd_knobs(self) -> dict[str, int]:
        return {"test-defer-fake": 1} if self.r1 else {}


@dataclass
class ReproOptions:
    drivers: Path
    sr_uuid: str
    tapdisk: Path
    tdtest_ctl: Path
    expect: str
    variant: Variant
    reset: str
    artifacts: Path
    run_shutdown: bool


@dataclass
class ReproHost:
    host: Host
    vm_uuid: str
    sr_uuid: str


@dataclass
class ReproVM:
    vm: VM
    host: Host
    snapshot: Snapshot
    userdevice: str
    target: int
    applied_knobs: dict[str, int] | None = None
    banner: str | None = None
    script_pushed: bool = False


@dataclass
class ReproDisk:
    vdi: VDI
    vbd: VBD
    number: int
    pid: int
    minor: int
    target: int
    devid: int | None


@dataclass
class ReproContext:
    host: Host
    vm: VM
    disk: ReproDisk
    bs: Blockstamp
    log: LogSlice
    options: ReproOptions
    deployment: TdtestDeployment
    guest: ReproVM
    artifacts: Path
    name: str

    @property
    def variant(self) -> Variant:
        return self.options.variant

    def tdtest(self, **kv: object) -> TdtestConfigResult:
        """tdtest_set() on the test VDI, with the variant's remediation keys added."""
        values: dict[str, object] = dict(self.variant.tdtest_fixes())
        values.update(kv)
        return tdtest_set(self.host, self.disk.vdi.uuid, values)

    def tdtest_events(self) -> list[TdtestEvent]:
        return [e for e in parse_tdtest_events(self.log.lines) if e.minor in (None, self.disk.minor)]

    def xenvbd_events(self) -> list[XenvbdEvent]:
        """xenvbd TDTEST lines for the test target (lines without target= are kept)."""
        return [e for e in parse_xenvbd_events(self.log.lines) if e.target in (None, self.disk.target)]

    def wait_tdtest(self, pred: Callable[[TdtestEvent], bool], timeout: float, what: str) -> TdtestEvent:
        deadline = time.monotonic() + timeout
        while True:
            for e in self.tdtest_events():
                if pred(e):
                    return e
            if time.monotonic() > deadline:
                raise TimeoutError(f"tdtest event not seen within {timeout}s: {what}")
            time.sleep(2)

    def save_json(self, name: str, data: object) -> Path:
        self.artifacts.mkdir(parents=True, exist_ok=True)
        p = self.artifacts / name
        p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return p


def tdtest_lba_window(prefix: str, window: Window) -> dict[str, int]:
    """tdtest `<prefix>_lba_start/_end` keys for a block window (sectors, half-open: the end is one past the last)."""
    return {f"{prefix}_lba_start": window.sector_start, f"{prefix}_lba_end": window.sector_end + 1}


def find_banner(lines: list[str]) -> str | None:
    for line in lines:
        m = XENVBD_RE_BANNER.search(line)
        if m:
            return m["variant"]
    return None


def check_banner(banner: str | None, variant: Variant) -> None:
    if banner is None:
        logging.warning("no xenvbd TDTEST banner seen in the host log; cannot confirm the driver build")
        return
    assert banner.lower() == variant.expected_banner.lower(), (
        f"xenvbd banner says variant {banner!r} but --repro-variant={variant.label!r} expects "
        f"{variant.expected_banner!r}: wrong driver zip?"
    )


CLASS_GREF_REISSUED_TO_RING = "gref reissued to a new ring page"


def classify_dead_intake(tdtest_events: list[TdtestEvent], xenvbd_events: list[XenvbdEvent]) -> list[dict[str, Any]]:
    """
    S-C: find dead-ring intakes whose grefs were reissued as a shared-ring page of a newer ring.

    A faked request's gref can come back as a page of the NEW ring after reconnect (tapdisk keeps its grant
    cache while it maps the dead ring, and the grant magazine is LIFO). A dead-ring write intake on such a gref
    copies ring contents instead of the data, so blockstamp sees CRC/garbage. For each `dead-intake` event
    this correlates its grefs with the grefs of every xenvbd `ring-connect` (plus `ring-connect-grefs`) line that
    came before it, and names the xenvbd requests that used the gref earlier. Both event lists must come from
    the same log lines (their `index` values are positions in the same slice).
    """
    connects = xenvbd_ring_connects(xenvbd_events)
    requests = group_xenvbd_requests(xenvbd_events)
    found = []
    for e in tdtest_events:
        if e.ev != "dead-intake":
            continue
        grefs = set(e.grefs)
        for rc in connects:
            overlap = grefs & set(rc.grefs)
            if not overlap or rc.index > e.index:
                continue
            owners = [
                {"event": r.event, "id": r.id, "ring": r.ring, "line_index": r.index}
                for r in requests
                if r.index < rc.index and (set(r.grefs) | set(r.igrefs)) & overlap
            ]
            found.append({
                "class": CLASS_GREF_REISSUED_TO_RING,
                "dead_intake": e.line,
                "tdtest_id": e.id,
                "grefs": sorted(overlap),
                "ring_connect": rc.line,
                "earlier_requests": owners[:10],
            })
    return found


def matching(rx: re.Pattern[str], lines: list[str]) -> list[tuple[int, re.Match[str]]]:
    return [(i, m) for i, m in ((i, rx.search(line)) for i, line in enumerate(lines)) if m]


# --------------------------------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------------------------------

@dataclass
class Evidence:
    scenario: str
    variant: str
    expect: str
    host_evidence: bool = False
    guest_evidence: bool = False
    reproduced: bool = False
    host: dict[str, Any] = field(default_factory=dict)
    guest: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.host_evidence and not self.guest_evidence


def problems_summary(result: BlockstampResult, window: Window, classes: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {"lba": p.lba, "class": p.cls, "detail": p.detail, "source": p.source, "hdr": p.hdr}
        for p in result.problems_in(window, classes)[:50]
    ]


def conclude(ctx: ReproContext, ev: Evidence) -> None:
    """Save the evidence next to the log slice and assert according to --repro-expect."""
    ctx.log.refresh()
    ev.paths["log_slice"] = ", ".join(str(p) for p in ctx.log.save())
    ev.paths["artifacts"] = str(ctx.artifacts)
    ev.host.setdefault("banner", ctx.guest.banner)
    path = ctx.save_json("evidence.json", asdict(ev))
    summary = (
        f"{ev.scenario} [{ev.variant}] expect={ev.expect}: reproduced={ev.reproduced} "
        f"host_evidence={ev.host_evidence} guest_evidence={ev.guest_evidence} notes={ev.notes} (see {path})"
    )
    logging.info(summary)
    if ev.expect == "reproduce":
        assert ev.reproduced, "not reproduced: " + summary
    elif ev.expect == "clean":
        assert ev.clean, "not clean: " + summary
