"""
Python side of scripts/guests/windows/blockstamp.ps1, the raw stamped-block corruption detector.

All block numbers are in 4 KiB units; block N is 512-byte sectors [8N, 8N + 7].
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from lib import commands
from lib.vm import VM

from typing import Any

BLOCK_SIZE = 4096
SECTOR_SIZE = 512
SECTORS_PER_BLOCK = BLOCK_SIZE // SECTOR_SIZE
RESERVED_BLOCKS = 256
MAX_THREADS = 4
MAX_IO_SIZE = 1 << 20
MAX_SECONDS = 300

SCRIPT_LOCAL = Path(__file__).parents[2] / "scripts" / "guests" / "windows" / "blockstamp.ps1"
GUEST_DIR = PureWindowsPath("C:\\blockstamp")
# Path of GUEST_DIR as seen by the guest's OpenSSH sftp server (same convention as VM.start_background_process).
SFTP_DIR = "/blockstamp"
GUEST_SCRIPT = GUEST_DIR / "blockstamp.ps1"
RESULT_PREFIX = "BLOCKSTAMP-RESULT "
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

PROBLEM_CLASSES = ("STALE", "MISDIRECTED", "CRC", "FUTURE", "UNWRITTEN", "READ-ERROR")


@dataclass(frozen=True)
class Window:
    """A range of 4 KiB blocks [start, start + count)."""

    start: int
    count: int

    def __post_init__(self) -> None:
        assert self.start >= RESERVED_BLOCKS, f"blocks below {RESERVED_BLOCKS} are reserved"
        assert self.count > 0

    @property
    def end(self) -> int:
        return self.start + self.count

    @property
    def sector_start(self) -> int:
        return self.start * SECTORS_PER_BLOCK

    @property
    def sector_end(self) -> int:
        """Last sector of the window (inclusive)."""
        return self.end * SECTORS_PER_BLOCK - 1

    def contains_block(self, lba: int) -> bool:
        return self.start <= lba < self.end

    def contains_sector(self, sector: int) -> bool:
        return self.sector_start <= sector <= self.sector_end

    @staticmethod
    def span(*windows: Window) -> Window:
        start = min(w.start for w in windows)
        return Window(start, max(w.end for w in windows) - start)


@dataclass
class BlockProblem:
    lba: int
    cls: str
    detail: str
    problem: bool
    source: str
    hdr: dict[str, Any]

    @property
    def hdr_lba(self) -> int | None:
        v = self.hdr.get("lba")
        return int(v) if v is not None else None

    @property
    def hdr_gen(self) -> int | None:
        v = self.hdr.get("gen")
        return int(v) if v is not None else None


@dataclass
class BlockstampResult:
    mode: str
    raw: dict[str, Any]
    output: str = ""
    entries: list[BlockProblem] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any], output: str = "") -> BlockstampResult:
        entries = []
        for key in ("problems", "mismatches"):
            for p in data.get(key) or []:
                entries.append(BlockProblem(
                    lba=int(p.get("lba", -1)),
                    cls=str(p.get("class", "")),
                    detail=str(p.get("detail", "")),
                    problem=bool(p.get("problem", True)),
                    source=str(p.get("source", key)),
                    hdr=dict(p.get("hdr") or {}),
                ))
        return cls(str(data.get("mode", "")), data, output, entries)

    @property
    def ok(self) -> bool:
        return bool(self.raw.get("ok"))

    @property
    def problems(self) -> list[BlockProblem]:
        return [e for e in self.entries if e.problem]

    @property
    def counts(self) -> dict[str, int]:
        return {k: int(v) for k, v in (self.raw.get("counts") or {}).items()}

    @property
    def status(self) -> str | None:
        return self.raw.get("status")

    @property
    def win32err(self) -> int:
        return int(self.raw.get("win32err", 0))

    @property
    def block_crcs(self) -> list[int]:
        return [int(c, 16) for c in self.raw.get("blockCrcs") or []]

    def problems_in(self, window: Window, classes: tuple[str, ...] = PROBLEM_CLASSES) -> list[BlockProblem]:
        return [p for p in self.problems if window.contains_block(p.lba) and p.cls in classes]


@dataclass
class BackgroundRun:
    mode: str
    out: PureWindowsPath
    pid: str
    timeout_secs: int
    started: float


class Blockstamp:
    """Drives blockstamp.ps1 on one RAW test disk of the guest, for one run id (one ledger)."""

    def __init__(self, vm: VM, disk_number: int, run_id: str, expect_size: int, artifacts_dir: Path) -> None:
        assert RUN_ID_RE.match(run_id), f"invalid run id {run_id!r}"
        self.vm = vm
        self.disk = int(disk_number)
        self.run_id = run_id
        self.expect_size = int(expect_size)
        self.artifacts_dir = artifacts_dir
        self._n = 0

    # ---------------------------------------------------------------- plumbing

    @staticmethod
    def push(vm: VM) -> None:
        """Upload blockstamp.ps1 to the guest and run its self-test (which touches no disk)."""
        vm.execute_powershell_script(f"New-Item -ItemType Directory -Force '{GUEST_DIR}' | Out-Null")
        assert vm.ip is not None
        commands.sftp(vm.ip, [f'put "{SCRIPT_LOCAL.as_posix()}" "{SFTP_DIR}/blockstamp.ps1"'])
        res = vm.execute_powershell_script(
            f"Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; & '{GUEST_SCRIPT}' -Mode selftest"
        )
        assert '"ok":true' in res, f"blockstamp self-test failed in the guest: {res}"

    def _out_path(self, mode: str) -> PureWindowsPath:
        self._n += 1
        return GUEST_DIR / f"{self.run_id}.{self._n:03d}.{mode}.json"

    def _command(self, mode: str, out: PureWindowsPath, args: dict[str, int | str | bool]) -> str:
        parts = [
            "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force;",
            f"& '{GUEST_SCRIPT}' -Mode {mode} -Disk {self.disk} -ExpectSize {self.expect_size} -Out '{out}'",
        ]
        if mode != "reset-device":
            parts.append(f"-Run {self.run_id}")
        for k, v in args.items():
            if isinstance(v, bool):
                if v:
                    parts.append(f"-{k}")
            else:
                parts.append(f"-{k} {int(v)}")
        return " ".join(parts)

    @staticmethod
    def _parse_output(output: str) -> dict[str, Any] | None:
        for line in reversed(output.splitlines()):
            if line.startswith(RESULT_PREFIX):
                return json.loads(line[len(RESULT_PREFIX):])
        return None

    def _run(self, mode: str, args: dict[str, int | str | bool]) -> BlockstampResult:
        out = self._out_path(mode)
        cmd = self._command(mode, out, args)
        logging.info("blockstamp %s %s", mode, args)
        res = self.vm.execute_powershell_script(cmd, simple_output=False)
        data = self._parse_output(res.stdout)
        if data is None:
            raise AssertionError(f"blockstamp {mode} failed (exit {res.returncode}): {res.stdout[-4000:]}")
        result = BlockstampResult.from_json(data, res.stdout)
        self._save(out.name, data)
        logging.info("blockstamp %s ok=%s %s", mode, result.ok, _summary(result))
        return result

    def _save(self, name: str, data: dict[str, Any]) -> None:
        d = self.artifacts_dir / "guest"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps(data, indent=1), encoding="utf-8")

    # ---------------------------------------------------------------- modes

    def fill(self, gen: int, window: Window, io_size: int = MAX_IO_SIZE) -> BlockstampResult:
        _check_bounds(1, io_size, 1)
        return self._run("fill", {"Gen": gen, "Start": window.start, "Count": window.count, "IoSize": io_size})

    def write_sync(self, gen: int, window: Window, flush: bool = True) -> BlockstampResult:
        assert window.count * BLOCK_SIZE <= MAX_IO_SIZE
        return self._run("write-sync", {"Gen": gen, "Lba": window.start, "Count": window.count, "Flush": flush})

    def verify(self, window: Window, io_size: int = MAX_IO_SIZE) -> BlockstampResult:
        _check_bounds(1, io_size, 1)
        return self._run("verify", {"Start": window.start, "Count": window.count, "IoSize": io_size})

    def reset_device(self) -> BlockstampResult:
        return self._run("reset-device", {})

    def load(self, gen: int, window: Window, *, threads: int, io_size: int, seconds: int,
             read_percent: int = 50) -> BlockstampResult:
        return self.wait(self.start_load(gen, window, threads=threads, io_size=io_size, seconds=seconds,
                                         read_percent=read_percent))

    def start_load(self, gen: int, window: Window, *, threads: int, io_size: int, seconds: int,
                   read_percent: int = 50) -> BackgroundRun:
        _check_bounds(threads, io_size, seconds)
        args: dict[str, int | str | bool] = {
            "Gen": gen, "Start": window.start, "Count": window.count, "Threads": threads, "IoSize": io_size,
            "Seconds": seconds, "ReadPercent": read_percent,
        }
        return self._start("load", args, timeout_secs=seconds + 240)

    def start_write_sync(self, gen: int, window: Window, flush: bool = True,
                         timeout_secs: int = 300) -> BackgroundRun:
        """write-sync in the background, for writes expected to stall (held or not taken by tapdisk)."""
        assert window.count * BLOCK_SIZE <= MAX_IO_SIZE
        args: dict[str, int | str | bool] = {"Gen": gen, "Lba": window.start, "Count": window.count, "Flush": flush}
        return self._start("write-sync", args, timeout_secs=timeout_secs)

    def _start(self, mode: str, args: dict[str, int | str | bool], timeout_secs: int) -> BackgroundRun:
        out = self._out_path(mode)
        cmd = self._command(mode, out, args) + f" *> '{out}.log'"
        logging.info("blockstamp %s (background) %s", mode, args)
        pid = self.vm.start_background_powershell(cmd)
        return BackgroundRun(mode, out, str(pid).strip(), timeout_secs, time.monotonic())

    def is_done(self, run: BackgroundRun) -> bool:
        return self.vm.execute_powershell_script(f"Test-Path '{run.out}'").strip() == "True"

    def wait(self, run: BackgroundRun) -> BlockstampResult:
        deadline = run.started + run.timeout_secs
        while not self.is_done(run):
            exited = run.pid.isdigit() and not self.vm.pid_exists(run.pid, winpid=True)
            if exited:
                time.sleep(2)
                if self.is_done(run):
                    break
            if exited or time.monotonic() > deadline:
                log = self.vm.execute_powershell_script(
                    f"Get-Content -Raw '{run.out}.log' -ErrorAction SilentlyContinue"
                )
                raise TimeoutError(f"blockstamp {run.mode} (pid {run.pid}) did not finish: {log[-4000:]}")
            time.sleep(3)
        text = self.vm.execute_powershell_script(f"Get-Content -Raw '{run.out}'")
        data = json.loads(text)
        result = BlockstampResult.from_json(data)
        self._save(run.out.name, data)
        logging.info("blockstamp %s ok=%s %s", run.mode, result.ok, _summary(result))
        return result

    # ---------------------------------------------------------------- artifacts

    def fetch_artifacts(self) -> list[Path]:
        """Copy the ledger and every JSON/log file of this run from the guest into the artifacts dir."""
        names = self.vm.execute_powershell_script(
            f"Get-ChildItem '{GUEST_DIR}' -File -Filter '{self.run_id}.*' | ForEach-Object {{ $_.Name }}"
        ).split()
        d = self.artifacts_dir / "guest"
        d.mkdir(parents=True, exist_ok=True)
        paths = []
        for name in names:
            local = d / name
            try:
                self.vm.sftp_get(f"{SFTP_DIR}/{name}", local.as_posix())
                paths.append(local)
            except commands.SSHCommandFailed as e:
                logging.warning("could not fetch %s: %s", name, e)
        return paths


def _check_bounds(threads: int, io_size: int, seconds: int) -> None:
    """Safety rule 4, enforced here as well as in the guest script."""
    if not 1 <= threads <= MAX_THREADS:
        raise ValueError(f"threads must be within 1..{MAX_THREADS}")
    if not BLOCK_SIZE <= io_size <= MAX_IO_SIZE or io_size % BLOCK_SIZE:
        raise ValueError(f"io_size must be a multiple of {BLOCK_SIZE} within {BLOCK_SIZE}..{MAX_IO_SIZE}")
    if not 1 <= seconds <= MAX_SECONDS:
        raise ValueError(f"seconds must be within 1..{MAX_SECONDS}")


def _summary(result: BlockstampResult) -> str:
    keys = ("counts", "problemCount", "readErrors", "okBlocks", "errBlocks", "writes", "writeErrors", "reads",
            "readMismatches", "errorCodes", "status", "win32err", "durationMs", "hungThreads", "fatal")
    return json.dumps({k: result.raw[k] for k in keys if k in result.raw})
