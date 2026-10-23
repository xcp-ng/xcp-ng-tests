"""
Host-side helpers for the tdtest-patched tapdisk used by the xenvbd shutdown-ordering repro.

Safety model (the test host is shared with production VMs):
- Only the tapdisk serving the test VDI is ever signalled or configured. Its PID and minor come from
  `tap-ctl list`, with exactly one row whose path contains the VDI UUID, and they are re-resolved on the
  host, in the same shell command, immediately before every signal. Real-time signals are only sent to a
  PID whose /proc/<pid>/exe is the tdtest binary (a stock tapdisk would be killed by them).
- Nothing here uses killall, pkill or broadcasts.
- The patched binary is only in place inside `swap_window`, which always restores the stock binary
  and checks its sha256 on the way out.
"""
from __future__ import annotations

import base64
import contextlib
import dataclasses
import hashlib
import json
import logging
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path

from lib.commands import SSHCommandFailed
from lib.host import Host

from typing import Callable, Generator, Iterable, Mapping

# --------------------------------------------------------------------------------------------------
# Host paths
# --------------------------------------------------------------------------------------------------

TDTEST_DIR = "/root/tdtest"
TDTEST_BIN = f"{TDTEST_DIR}/tapdisk.tdtest"
TDTEST_CTL = f"{TDTEST_DIR}/tdtest-ctl"
STOCK_BIN = f"{TDTEST_DIR}/tapdisk.stock"
STOCK_SHA_FILE = f"{TDTEST_DIR}/tapdisk.stock.sha256"
LIVE_BIN = "/usr/libexec/tapdisk"
LIVE_BIN_TMP = "/usr/libexec/tapdisk.xcpng-repro-new"
CONF_DIR = "/run/tapdisk-test"

# Phase 0: tapdisk (facility daemon), the guest PV driver output relayed by QEMU and `logger -p daemon.*`
# all land in daemon.log on XCP-ng 8.3. Nothing is logged under /var/log/xen/.
HOST_LOG = "/var/log/daemon.log"
LOG_TAG = "xcpng-repro"
LOGGER_PRIORITY = "daemon.notice"

# --------------------------------------------------------------------------------------------------
# Log formats. ALL tapdisk/tdtest/host-side patterns live here so they can be aligned with the real
# output in one place. The xenvbd (guest) patterns live in lib/windows/test_drivers.py.
# --------------------------------------------------------------------------------------------------

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Line selectors, written as POSIX ERE so that the same text works for `grep -E` on the host and for
# Python `re`. `{pids}` / `{domids}` are `|`-joined lists of integers.
TAPDISK_IDENT_ERE = r"tapdisk\[({pids})\]: "
QEMU_IDENT_ERE = r"qemu-dm-({domids})\["
REPRO_TAG_ERE = r"xcpng-repro(\[[0-9]+\])?: "

# tdtest control acknowledgements.
TD_RE_CONFIG_OK = re.compile(
    r"tdtest: config minor=(?P<minor>\d+) vdi=(?P<vdi>[0-9a-fA-F-]{36}) seq=(?P<seq>\d+)"
)
TD_RE_CONFIG_REJECTED = re.compile(r"tdtest: config rejected(?P<reason>.*)$")
TD_RE_BANNER = re.compile(r"tdtest: v1 variant=(?P<variant>\S+)")
# tdtest data events: `tdtest: ... ev=<name> key=value ...`.
TD_RE_ANY = re.compile(r"tdtest: (?P<body>.*)$")
TD_RE_EVENT = re.compile(r"tdtest: (?P<body>.*\bev=(?P<ev>[\w-]+).*)$")
TD_KV_RE = re.compile(r"(?P<k>[A-Za-z_][\w/]*)=(?P<v>[^\s,][^\s]*)")
TDTEST_EVENTS = frozenset({
    "intake", "hold", "release", "copyback", "copyback-skipped-dead", "complete", "connect", "disconnect",
    "dead-intake", "gate-open", "gate-close",
})
# Stock tapdisk lines (RING_DEBUG/RING_ERR prefix them with "<domid>/<devid>, ring=<ptr>: ").
TD_RE_RING_PREFIX = r"(?:(?P<domid>\d+)/(?P<devid>\d+), ring=(?P<ring>[^:\s]+): )?"
TD_RE_DISCONNECT_PENDING = re.compile(
    TD_RE_RING_PREFIX + r"disconnect from ring with (?P<n>\d+) pending requests"
)
TD_RE_DEAD_RING_DESTROY = re.compile(TD_RE_RING_PREFIX + r"destroying dead ring")
TD_RE_GRANT_COPY_FAIL = re.compile(
    TD_RE_RING_PREFIX
    + r"(?:tap-err:\S+: )?(?:req (?P<sid>\d+): )?failed to grant-copy (?:request (?P<rid>\d+)|segment (?P<seg>\d+))"
)
# Number bases used by tdtest fields. 0 means "decimal unless 0x-prefixed".
TD_ID_BASE = 0
TD_GREF_BASE = 0
TD_SECTOR_BASE = 0
TD_CRC_BASE = 16

# tdtest config file keys (see the Phase 2 plan). `vdi` and `seq` are always written by tdtest_set().
TDTEST_KEYS = frozenset({
    "log", "log_lba_start", "log_lba_end",
    "read_hold_ms", "read_hold_lba_start", "read_hold_lba_end", "read_hold_max",
    "write_hold_ms", "write_hold_lba_start", "write_hold_lba_end", "write_hold_max",
    "intake_pause", "dead_chkrng_kick", "fix_dead_intake", "fix_gate_new_ring",
})
# Keys that trigger a one-shot action on reload and must not be carried over to later configs.
TDTEST_ONE_SHOT_KEYS = frozenset({"dead_chkrng_kick"})
# LBA windows are in 512-byte sectors; the end is written as the last sector of the window.
TDTEST_LBA_END_INCLUSIVE = True
TDTEST_ACK_TIMEOUT_S = 15

# Exit codes of the on-host guard.
GUARD_EXIT_COUNT = 97
GUARD_EXIT_CHANGED = 98
GUARD_EXIT_NOT_PATCHED = 99


class TapdiskGuardError(Exception):
    """A safety guard refused to act on a tapdisk."""


class TdtestSafetyError(Exception):
    """The host is not in a state where it is safe to proceed; a human must look at it."""


class TapdiskRestoreError(Exception):
    """The stock tapdisk binary could not be restored or verified after a swap window."""


def parse_num(value: str, base: int) -> int:
    if base == 0:
        return int(value, 16) if value.lower().startswith("0x") else int(value, 10)
    return int(value, base)


def _check_uuid(uuid: str) -> str:
    if not UUID_RE.match(uuid):
        raise ValueError(f"not a UUID: {uuid!r}")
    return uuid.lower()


def sha256_local(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def host_sha256(host: Host, path: str) -> str:
    return host.ssh(f"sha256sum < {shlex.quote(path)}").split()[0]


def host_now(host: Host) -> float:
    return float(host.ssh("date +%s.%N"))


def host_log(host: Host, message: str) -> None:
    """Write a marker line to the host log with `logger -t xcpng-repro`."""
    host.ssh(f"logger -p {LOGGER_PRIORITY} -t {LOG_TAG} -- {shlex.quote(message)}")


# --------------------------------------------------------------------------------------------------
# tap-ctl
# --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TapdiskInfo:
    pid: int
    minor: int
    path: str


def parse_tap_ctl_list(output: str) -> list[TapdiskInfo]:
    """Parse `tap-ctl list`, in its key=value form (no tty) or its table form."""
    rows = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if "pid=" in line:
            kv = dict(re.findall(r"(\w+)=(\S+)", line))
            if "pid" not in kv or "minor" not in kv:
                continue
            path = kv.get("args", "-")
            rows.append(TapdiskInfo(int(kv["pid"]), int(kv["minor"]), path))
        else:
            fields = line.split(None, 4)
            if len(fields) == 5 and fields[0].isdigit() and fields[1].isdigit():
                rows.append(TapdiskInfo(int(fields[0]), int(fields[1]), f"{fields[3]}:{fields[4]}"))
    return rows


def find_tapdisk(host: Host, vdi_uuid: str) -> TapdiskInfo:
    """Return the only tapdisk whose image path contains the VDI UUID; refuse anything else."""
    vdi_uuid = _check_uuid(vdi_uuid)
    rows = [r for r in parse_tap_ctl_list(host.ssh("tap-ctl list")) if vdi_uuid in r.path.lower()]
    if len(rows) != 1:
        raise TapdiskGuardError(f"expected exactly one tapdisk for VDI {vdi_uuid}, found {len(rows)}: {rows}")
    return rows[0]


def _guard_script(vdi_uuid: str, pid: int, minor: int, require_patched: bool) -> str:
    """Shell function `_guard` that re-resolves the test tapdisk on the host and checks it is unchanged."""
    return f"""
VDI={shlex.quote(_check_uuid(vdi_uuid))}
EXP_PID={int(pid)}
EXP_MINOR={int(minor)}
_guard() {{
    rows=$(tap-ctl list | grep -F -- "$VDI" || true)
    n=$(printf '%s\\n' "$rows" | grep -c 'pid=' || true)
    if [ "$n" != 1 ]; then echo "GUARD: $n tapdisk rows match $VDI" >&2; exit {GUARD_EXIT_COUNT}; fi
    pid=$(printf '%s\\n' "$rows" | sed -n 's/.*pid=\\([0-9][0-9]*\\).*/\\1/p')
    minor=$(printf '%s\\n' "$rows" | sed -n 's/.*minor=\\([0-9][0-9]*\\).*/\\1/p')
    if [ "$pid" != "$EXP_PID" ] || [ "$minor" != "$EXP_MINOR" ]; then
        echo "GUARD: test tapdisk changed to pid=$pid minor=$minor" >&2; exit {GUARD_EXIT_CHANGED}
    fi
    if [ {1 if require_patched else 0} = 1 ]; then
        a=$(sha256sum < /proc/$pid/exe | cut -d' ' -f1)
        b=$(sha256sum < {TDTEST_BIN} | cut -d' ' -f1)
        if [ "$a" != "$b" ]; then
            echo "GUARD: pid $pid does not run the tdtest binary" >&2; exit {GUARD_EXIT_NOT_PATCHED}
        fi
    fi
}}
"""


def _run_guarded(host: Host, script: str) -> str:
    try:
        return host.ssh(script)
    except SSHCommandFailed as e:
        if e.returncode in (GUARD_EXIT_COUNT, GUARD_EXIT_CHANGED, GUARD_EXIT_NOT_PATCHED):
            raise TapdiskGuardError(f"on-host guard refused to act (exit {e.returncode}): {e.stdout}") from e
        raise


# --------------------------------------------------------------------------------------------------
# Deployment and swap windows
# --------------------------------------------------------------------------------------------------

@dataclass
class TdtestDeployment:
    stock_sha256: str
    tdtest_sha256: str
    ctl_sha256: str
    baseline: dict[str, str]
    swap_windows: list[dict[str, object]] = field(default_factory=list)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dataclasses.asdict(self), indent=2))


def record_baseline(host: Host) -> dict[str, str]:
    """Rule 6: record the package, binary hash and versions before the first swap."""
    q = shlex.quote(LIVE_BIN)
    baseline = {
        "time": host.ssh("date -Is"),
        "rpm_q_blktap": host.ssh("rpm -q blktap"),
        "rpm_V_blktap": host.ssh("rpm -V blktap || true"),
        "sha256_live_tapdisk": host_sha256(host, LIVE_BIN),
        "live_tapdisk_stat": host.ssh(f"stat -c '%a %U:%G %s %Y' {q}"),
        "os_release": host.ssh(". /etc/os-release && echo \"$PRETTY_NAME (VERSION_ID=$VERSION_ID)\""),
        "uname_r": host.ssh("uname -r"),
    }
    for key, value in baseline.items():
        logging.info("host baseline %s: %s", key, value)
    return baseline


def _rpm_v_flags(rpm_v: str, path: str) -> str | None:
    """Return the rpm -V flags reported for path, or None if the file is not reported."""
    for line in rpm_v.splitlines():
        parts = line.split()
        if parts and parts[-1] == path:
            return parts[0]
    return None


def deploy_tdtest(host: Host, tapdisk_local: Path, ctl_local: Path) -> TdtestDeployment:
    """
    Upload the tdtest artifacts to /root/tdtest/ and keep a verified stock copy of the live tapdisk.

    Refuses (TdtestSafetyError) if the live tapdisk does not match the RPM, unless it equals the recorded
    stock hash, or if a previously saved stock copy no longer matches the live binary.
    """
    baseline = record_baseline(host)
    live_sha = baseline["sha256_live_tapdisk"]
    flags = _rpm_v_flags(baseline["rpm_V_blktap"], LIVE_BIN)
    # Size, digest or mode changes mean the content or permissions differ from the RPM. An mtime-only
    # difference ("T") is tolerated because the digest still matches.
    modified = flags is not None and (flags == "missing" or any(c in flags for c in "S5M"))

    host.ssh(f"mkdir -p {TDTEST_DIR} && chmod 700 {TDTEST_DIR}")
    has_stock = host.ssh_with_result(f"test -f {STOCK_BIN}").returncode == 0
    if has_stock:
        stock_sha = host_sha256(host, STOCK_BIN)
        recorded = host.ssh(f"cat {STOCK_SHA_FILE} 2>/dev/null || true").split()
        if not recorded or recorded[0] != stock_sha:
            raise TdtestSafetyError(
                f"{STOCK_BIN} does not match its recorded hash in {STOCK_SHA_FILE}; check the host by hand"
            )
        if live_sha != stock_sha:
            if modified:
                raise TdtestSafetyError(
                    f"{LIVE_BIN} is modified (rpm -V: {flags}) and differs from {STOCK_BIN}. "
                    f"Restore it (cp -p {STOCK_BIN} {LIVE_BIN}, or yum reinstall blktap) before running."
                )
            raise TdtestSafetyError(
                f"{LIVE_BIN} matches the RPM but not {STOCK_BIN}: blktap was probably updated. "
                f"Remove {STOCK_BIN} and {STOCK_SHA_FILE} after checking, then rebuild tdtest for the new source."
            )
    else:
        if modified:
            raise TdtestSafetyError(
                f"{LIVE_BIN} does not match the blktap RPM (rpm -V: {flags}) and no stock copy is recorded. "
                "Run `yum reinstall blktap` (after asking) before running."
            )
        host.ssh(f"cp -p {LIVE_BIN} {STOCK_BIN} && sha256sum < {STOCK_BIN} > {STOCK_SHA_FILE}")
        stock_sha = host_sha256(host, STOCK_BIN)
        if stock_sha != live_sha:
            raise TdtestSafetyError(f"copying {LIVE_BIN} to {STOCK_BIN} gave a different hash")

    tdtest_sha = _upload_verified(host, tapdisk_local, TDTEST_BIN)
    ctl_sha = _upload_verified(host, ctl_local, TDTEST_CTL)
    if tdtest_sha == stock_sha:
        raise TdtestSafetyError("the tdtest tapdisk has the same hash as the stock one: wrong artifact?")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    record = json.dumps({**baseline, "stock_sha256": stock_sha, "tdtest_sha256": tdtest_sha,
                         "ctl_sha256": ctl_sha}, indent=2)
    _write_host_file(host, f"{TDTEST_DIR}/baseline-{stamp}.json", record)
    return TdtestDeployment(stock_sha, tdtest_sha, ctl_sha, baseline)


def _write_host_file(host: Host, path: str, text: str) -> None:
    """Write a text file on the host atomically, without scp'ing an open temporary file."""
    b64 = base64.b64encode(text.encode()).decode()
    q = shlex.quote(path)
    tmp = shlex.quote(path + ".tmp")
    host.ssh(f"printf %s {b64} | base64 -d > {tmp} && mv -f {tmp} {q}")


def _upload_verified(host: Host, local: Path, remote: str) -> str:
    want = sha256_local(local)
    if host.ssh_with_result(f"test -f {remote}").returncode == 0 and host_sha256(host, remote) == want:
        logging.info("%s already up to date (%s)", remote, want)
    else:
        tmp = remote + ".upload"
        host.scp(local.as_posix(), tmp)
        got = host_sha256(host, tmp)
        if got != want:
            host.ssh(f"rm -f {tmp}")
            raise TdtestSafetyError(f"upload of {local} to {remote} is corrupted ({got} != {want})")
        host.ssh(f"chmod 755 {tmp} && mv -f {tmp} {remote}")
    return want


def check_stock_restored(host: Host, deployment: TdtestDeployment) -> str:
    sha = host_sha256(host, LIVE_BIN)
    if sha != deployment.stock_sha256:
        raise TapdiskRestoreError(f"{LIVE_BIN} sha256 {sha} != stock {deployment.stock_sha256}")
    return sha


@contextlib.contextmanager
def swap_window(host: Host, deployment: TdtestDeployment, label: str = "") -> Generator[None, None, None]:
    """
    Put the tdtest tapdisk in place for the duration of the block, then restore the stock binary.

    Tapdisks spawned inside the window (normally only the test VBD's) run the patched but inert binary.
    The stock binary is restored in `finally` and its sha256 is checked; failure raises TapdiskRestoreError.
    """
    record: dict[str, object] = {"label": label}
    host_log(host, f"swap-window begin {label}")
    record["begin"] = host_now(host)
    restored = False
    try:
        out = host.ssh(
            f"set -e; cp {TDTEST_BIN} {LIVE_BIN_TMP}; chmod --reference={LIVE_BIN} {LIVE_BIN_TMP}; "
            f"chown --reference={LIVE_BIN} {LIVE_BIN_TMP}; mv -f {LIVE_BIN_TMP} {LIVE_BIN}; "
            f"date +%s.%N; sha256sum < {LIVE_BIN}"
        ).split()
        record["swapped_in"] = float(out[0])
        if out[1] != deployment.tdtest_sha256:
            raise TapdiskRestoreError(f"swap-in produced unexpected sha256 {out[1]}")
        yield
    finally:
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                out = host.ssh(
                    f"set -e; cp -p {STOCK_BIN} {LIVE_BIN_TMP}; mv -f {LIVE_BIN_TMP} {LIVE_BIN}; "
                    f"date +%s.%N; sha256sum < {LIVE_BIN}"
                ).split()
                record["swapped_out"] = float(out[0])
                record["restored_sha256"] = out[1]
                if out[1] == deployment.stock_sha256:
                    restored = True
                    break
                last_error = TapdiskRestoreError(f"restored sha256 {out[1]} != stock {deployment.stock_sha256}")
            except SSHCommandFailed as e:
                last_error = e
            time.sleep(2)
        record["restored"] = restored
        deployment.swap_windows.append(record)
        logging.info("swap window %s", record)
        try:
            host_log(host, f"swap-window end {label} restored={restored}")
        except SSHCommandFailed:
            pass
        if not restored:
            raise TapdiskRestoreError(
                f"STOCK TAPDISK NOT RESTORED on {host}: run `cp -p {STOCK_BIN} {LIVE_BIN}` or "
                f"`yum reinstall blktap` now. Last error: {last_error}"
            )


def assert_patched(host: Host, pid: int, deployment: TdtestDeployment) -> None:
    sha = host_sha256(host, f"/proc/{int(pid)}/exe")
    assert sha == deployment.tdtest_sha256, f"tapdisk {pid} runs {sha}, not the tdtest binary"


def patched_tapdisk_pids(host: Host, deployment: TdtestDeployment) -> list[int]:
    """Read-only report: PIDs of every tapdisk still running the tdtest binary."""
    out = host.ssh(
        "for p in $(pgrep -x tapdisk); do echo \"$p $(sha256sum < /proc/$p/exe | cut -d' ' -f1)\"; done"
    )
    pids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == deployment.tdtest_sha256:
            pids.append(int(parts[0]))
    return pids


# --------------------------------------------------------------------------------------------------
# tdtest control
# --------------------------------------------------------------------------------------------------

@dataclass
class _ConfState:
    seq: int = 0
    values: dict[str, str] = field(default_factory=dict)


_CONF_STATE: dict[tuple[str, str], _ConfState] = {}


@dataclass
class TdtestConfigResult:
    accepted: bool
    pid: int
    minor: int
    seq: int
    config: str
    line: str


def _format_value(key: str, value: object) -> str:
    if key == "intake_pause" and value in ("current", "0", 0):
        return str(value)
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int) and value >= 0:
        return str(value)
    raise ValueError(f"invalid tdtest value {key}={value!r}")


def _log_offset(host: Host) -> int:
    return int(host.ssh(f"stat -c %s {HOST_LOG}"))


def _wait_tdtest_ack(host: Host, pid: int, offset: int, minor: int, vdi: str, seq: int,
                     timeout: float) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    while True:
        out = host.ssh(
            f"tail -c +{offset + 1} {HOST_LOG} | grep -a -F 'tapdisk[{pid}]: ' | grep -a -F 'tdtest: config' || true"
        )
        for line in out.splitlines():
            m = TD_RE_CONFIG_OK.search(line)
            if m and int(m["minor"]) == minor and m["vdi"].lower() == vdi and int(m["seq"]) == seq:
                return True, line
            if TD_RE_CONFIG_REJECTED.search(line):
                return False, line
        if time.monotonic() > deadline:
            raise TimeoutError(f"no tdtest config acknowledgement from tapdisk {pid} for seq={seq}")
        time.sleep(1)


def tdtest_send_config(host: Host, vdi_uuid: str, values: dict[str, str], *, conf_vdi: str | None = None,
                       timeout: float = TDTEST_ACK_TIMEOUT_S) -> TdtestConfigResult:
    """
    Low-level: write /run/tapdisk-test/<minor>.conf atomically and signal `reload` to the test tapdisk.

    conf_vdi overrides the `vdi=` line (used to check that a wrong VDI is rejected).
    """
    vdi_uuid = _check_uuid(vdi_uuid)
    conf_vdi = _check_uuid(conf_vdi) if conf_vdi else vdi_uuid
    info = find_tapdisk(host, vdi_uuid)
    key = (str(host), vdi_uuid)
    state = _CONF_STATE.setdefault(key, _ConfState())
    seq = state.seq + 1
    lines = [f"vdi={conf_vdi}", f"seq={seq}"] + [f"{k}={v}" for k, v in sorted(values.items())]
    config = "\n".join(lines) + "\n"
    b64 = base64.b64encode(config.encode()).decode()
    conf = f"{CONF_DIR}/{info.minor}.conf"
    tmp = f"{CONF_DIR}/.{info.minor}.conf.tmp"
    script = _guard_script(vdi_uuid, info.pid, info.minor, require_patched=True) + f"""
set -e
_guard
off=$(stat -c %s {HOST_LOG})
mkdir -p {CONF_DIR}
printf %s {b64} | base64 -d > {tmp}
mv -f {tmp} {conf}
_guard
{TDTEST_CTL} "$pid" "$minor" reload
echo "$off"
"""
    logging.info("tdtest reload pid=%d minor=%d seq=%d: %s", info.pid, info.minor, seq, " ".join(lines[2:]))
    offset = int(_run_guarded(host, script).split()[-1])
    state.seq = seq
    accepted, line = _wait_tdtest_ack(host, info.pid, offset, info.minor, conf_vdi, seq, timeout)
    logging.info("tdtest config %s: %s", "accepted" if accepted else "REJECTED", line)
    return TdtestConfigResult(accepted, info.pid, info.minor, seq, config, line)


def tdtest_set(host: Host, vdi_uuid: str, settings: Mapping[str, object] | None = None, *, replace: bool = False,
               timeout: float = TDTEST_ACK_TIMEOUT_S, **kv: object) -> TdtestConfigResult:
    """
    Address the test tapdisk with tdtest settings and wait for `tdtest: config ... seq=` (fail on rejection).

    Settings come from `settings` and/or keyword arguments. By default the new keys are merged into the
    previous config of this VDI, so the file always holds the full desired state; one-shot keys
    (dead_chkrng_kick) are not carried over. replace=True starts afresh.
    """
    vdi_uuid = _check_uuid(vdi_uuid)
    kv = {**(settings or {}), **kv}
    unknown = set(kv) - TDTEST_KEYS
    if unknown:
        raise ValueError(f"unknown tdtest keys: {sorted(unknown)}")
    state = _CONF_STATE.setdefault((str(host), vdi_uuid), _ConfState())
    values = {} if replace else {k: v for k, v in state.values.items() if k not in TDTEST_ONE_SHOT_KEYS}
    values.update({k: _format_value(k, v) for k, v in kv.items()})
    result = tdtest_send_config(host, vdi_uuid, values, timeout=timeout)
    if not result.accepted:
        raise AssertionError(f"tdtest config rejected by tapdisk {result.pid}: {result.line}")
    state.values = values
    return result


def tdtest_off(host: Host, vdi_uuid: str) -> None:
    """Send `off` to the test tapdisk (if it still exists and runs tdtest) and remove its config file."""
    vdi_uuid = _check_uuid(vdi_uuid)
    _CONF_STATE.pop((str(host), vdi_uuid), None)
    try:
        info = find_tapdisk(host, vdi_uuid)
    except TapdiskGuardError as e:
        logging.info("tdtest_off: %s", e)
        return
    script = _guard_script(vdi_uuid, info.pid, info.minor, require_patched=True) + f"""
set -e
_guard
{TDTEST_CTL} "$pid" "$minor" off
"""
    try:
        _run_guarded(host, script)
    except TapdiskGuardError as e:
        logging.warning("tdtest_off: not signalling: %s", e)
    remove_tdtest_conf(host, info.minor, vdi_uuid)


def remove_tdtest_conf(host: Host, minor: int, vdi_uuid: str) -> None:
    """Remove /run/tapdisk-test/<minor>.conf, but only if it was written for this VDI."""
    conf = f"{CONF_DIR}/{int(minor)}.conf"
    host.ssh(f"if grep -qx 'vdi={_check_uuid(vdi_uuid)}' {conf} 2>/dev/null; then rm -f {conf}; fi")


def sigusr1_dump(host: Host, vdi_uuid: str, expected_pid: int | None = None) -> int:
    """Ask the test tapdisk (only) for its SIGUSR1 debug dump. Returns its PID."""
    info = find_tapdisk(host, vdi_uuid)
    if expected_pid is not None and info.pid != expected_pid:
        raise TapdiskGuardError(f"test tapdisk is now pid {info.pid}, expected {expected_pid}")
    script = _guard_script(vdi_uuid, info.pid, info.minor, require_patched=False) + """
set -e
_guard
kill -USR1 "$pid"
"""
    _run_guarded(host, script)
    return info.pid


# --------------------------------------------------------------------------------------------------
# Parsed events
# --------------------------------------------------------------------------------------------------

@dataclass
class TdtestEvent:
    index: int
    line: str
    ev: str
    fields: dict[str, str]

    def get_int(self, key: str, base: int = 0) -> int | None:
        v = self.fields.get(key)
        if v is None:
            return None
        try:
            return parse_num(v, base)
        except ValueError:
            return None

    @property
    def id(self) -> int | None:
        return self.get_int("id", TD_ID_BASE)

    @property
    def minor(self) -> int | None:
        return self.get_int("minor")

    @property
    def sector(self) -> int | None:
        return self.get_int("sector", TD_SECTOR_BASE)

    @property
    def ring(self) -> str | None:
        return self.fields.get("ring")

    @property
    def dead(self) -> bool | None:
        v = self.fields.get("dead")
        return None if v is None else v not in ("0", "false", "no")

    @property
    def grefs(self) -> list[int]:
        return _num_list(self.fields.get("grefs"), TD_GREF_BASE)

    @property
    def crcs(self) -> list[int]:
        return _num_list(self.fields.get("crc"), TD_CRC_BASE)


def _num_list(value: str | None, base: int) -> list[int]:
    if not value:
        return []
    out = []
    for part in value.strip(",").split(","):
        try:
            out.append(parse_num(part, base))
        except ValueError:
            pass
    return out


def parse_tdtest_events(lines: Iterable[str]) -> list[TdtestEvent]:
    events = []
    for i, line in enumerate(lines):
        m = TD_RE_EVENT.search(line)
        if m:
            fields = {kv["k"]: kv["v"] for kv in TD_KV_RE.finditer(m["body"])}
            events.append(TdtestEvent(i, line, m["ev"], fields))
    return events


# --------------------------------------------------------------------------------------------------
# Log slices
# --------------------------------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name).strip("_")[:150] or "slice"


class LogSlice:
    """
    Slice of the host log between BEGIN and END markers, restricted to the test VM and test tapdisk.

    Only lines from `tapdisk[<test pid>]`, `qemu-dm-<test domid>[` and the `xcpng-repro` tag are ever
    collected (the filter runs on the host), so other VMs' lines never leave the host. The VM's domid
    changes on every reboot, so `domid_fn` is called again on every refresh and all domids seen are kept.
    """

    def __init__(self, host: Host, name: str, *, domid_fn: Callable[[], str | int | None] | None = None,
                 pids: Iterable[int] = (), out_dirs: Iterable[Path] = (), source: str = HOST_LOG) -> None:
        self.host = host
        self.name = name
        self.domid_fn = domid_fn
        self.pids: set[int] = {int(p) for p in pids}
        self.domids: set[int] = set()
        self.out_dirs = list(out_dirs)
        self.source = source
        self.inode = ""
        self.offset = 0
        self.closed = False
        self._lines: list[str] = []
        self.paths: list[Path] = []

    def add_pid(self, pid: int) -> None:
        self.pids.add(int(pid))

    def add_domid(self, domid: str | int | None) -> None:
        if domid is None:
            return
        try:
            value = int(domid)
        except ValueError:
            return
        if value > 0:
            self.domids.add(value)

    def _poll_domid(self) -> None:
        if self.domid_fn is not None:
            try:
                self.add_domid(self.domid_fn())
            except Exception as e: # the VM may be halted or rebooting
                logging.debug("LogSlice: cannot read domid: %s", e)

    def filter_ere(self) -> str:
        parts = [REPRO_TAG_ERE]
        if self.pids:
            parts.append(TAPDISK_IDENT_ERE.format(pids="|".join(str(p) for p in sorted(self.pids))))
        if self.domids:
            parts.append(QEMU_IDENT_ERE.format(domids="|".join(str(d) for d in sorted(self.domids))))
        return "|".join(f"({p})" for p in parts)

    def mark(self, text: str) -> None:
        host_log(self.host, f"{text} [{self.name}]")

    def __enter__(self) -> LogSlice:
        self._poll_domid()
        q = shlex.quote(self.source)
        self.inode, size = self.host.ssh(f"stat -c '%i %s' {q}").split()
        self.offset = int(size)
        host_log(self.host, f"BEGIN {self.name}")
        return self

    def refresh(self) -> list[str]:
        """Fetch the (filtered) slice so far."""
        self._poll_domid()
        q = shlex.quote(self.source)
        q1 = shlex.quote(self.source + ".1")
        ere = self.filter_ere()
        script = f"""
cur=$(stat -c %i {q}); size=$(stat -c %s {q})
{{
if [ "$cur" = "{self.inode}" ] && [ "$size" -ge {self.offset} ]; then
    tail -c +{self.offset + 1} {q}
else
    # rotated (or copy-truncated) since BEGIN
    if [ -f {q1} ]; then tail -c +{self.offset + 1} {q1}; fi
    cat {q}
fi
}} | grep -a -E -- {shlex.quote(ere)} || true
"""
        out = self.host.ssh(script)
        pattern = re.compile(ere)
        self._lines = [line for line in out.splitlines() if pattern.search(line)]
        return self._lines

    @property
    def lines(self) -> list[str]:
        if not self.closed:
            self.refresh()
        return self._lines

    def grep(self, pattern: str | re.Pattern[str]) -> list[re.Match[str]]:
        rx = re.compile(pattern) if isinstance(pattern, str) else pattern
        return [m for m in (rx.search(line) for line in self.lines) if m]

    def find(self, pattern: str | re.Pattern[str]) -> list[tuple[int, re.Match[str]]]:
        """Like grep() but also returns each line's index in the slice (its order of arrival)."""
        rx = re.compile(pattern) if isinstance(pattern, str) else pattern
        return [(i, m) for i, m in ((i, rx.search(line)) for i, line in enumerate(self.lines)) if m]

    def wait_for(self, pattern: str | re.Pattern[str], timeout: float, count: int = 1,
                 what: str | None = None) -> list[re.Match[str]]:
        deadline = time.monotonic() + timeout
        while True:
            found = self.grep(pattern)
            if len(found) >= count:
                return found
            if time.monotonic() > deadline:
                raise TimeoutError(f"log line not seen within {timeout}s: {what or pattern}")
            time.sleep(2)

    def __exit__(self, *exc: object) -> None:
        try:
            host_log(self.host, f"END {self.name}")
            self.refresh()
        finally:
            self.closed = True
            self.save()

    def save(self) -> list[Path]:
        text = "\n".join(self._lines) + "\n"
        self.paths = []
        for d in self.out_dirs:
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{_safe_name(self.name)}.host.log"
            p.write_text(text, encoding="utf-8")
            self.paths.append(p)
        return self.paths
