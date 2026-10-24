"""
Install test-signed xenvbd repro driver builds on a Windows VM and configure their registry knobs.

The VM must be the designated test VM. These helpers never migrate it: every start uses `on=<test host>`
and residence is checked after every boot.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from lib import commands
from lib.common import strtobool, wait_for
from lib.host import Host
from lib.tapdisk_test import parse_num
from lib.vm import VM

from . import WINDOWS_SHUTDOWN_COMMAND, enable_testsign, wait_for_vm_running_and_ssh_up_without_tools

from typing import Iterable, Mapping

INSTALL_DRIVERS_PS1 = Path(__file__).parents[2] / "scripts" / "guests" / "windows" / "install-drivers.ps1"
GUEST_DRIVER_DIR = PureWindowsPath("C:\\testdrivers")

XENVBD_PARAMETERS_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Services\xenvbd\Parameters"
XEN_PARAMETERS_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Services\XEN\Parameters"
DISK_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Services\Disk"
CRASH_CONTROL_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl"
MEMORY_MANAGEMENT_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"

# xenvbd test knobs (DWORDs under Services\xenvbd\Parameters), all off/default unless set.
XENVBD_TEST_KNOBS = frozenset({
    "test-disable-wait-ms", "test-close-delay-ms", "test-defer-fake", "test-log",
    "test-log-sector-start", "test-log-sector-end", "test-targets",
})
XENVBD_CLOSE_DELAY_MAX_MS = 5000
XENVBD_DISABLE_WAIT_MAX_MS = 5000
DISK_TIMEOUT_VALUE_S = 10
QEMU_LOG_LEVEL = ("INFO", "WARNING", "ERROR", "CRITICAL")

# --------------------------------------------------------------------------------------------------
# xenvbd log formats. ALL guest-driver patterns live here so they can be aligned with the real output.
# Guest lines reach daemon.log as
#   qemu-dm-<domid>[<pid>]: <pid>@<epoch.us>:xen_platform_log xen platform: xenvbd|<Function>: TDTEST <event> <k=v>...
# `id=` is lowercase hex without 0x, `t=` is guest ms, `grefs=` are comma-separated decimals, and lines are
# truncated at 255 characters. A request line (submit, defer, rsp, harvest, fake, deferred-fake) is followed by
# `<event>-grefs ... first=<n> grefs=...` lines (and `<event>-igrefs` for indirect pages) with the same
# target, ring and id. The event token has to match exactly: `fake` is not `fake-grefs` or `deferred-fake`.
# --------------------------------------------------------------------------------------------------

XENVBD_TOKEN = "TDTEST"
XENVBD_RE_ANY = re.compile(r"xenvbd\|(?P<func>[^:]*): TDTEST (?P<event>[\w-]+)(?: (?P<body>.*))?$")
XENVBD_RE_BANNER = re.compile(r"TDTEST banner.*variant=(?P<variant>[\w.+-]+)")
XENVBD_RE_RESET = re.compile(r"TDTEST TargetReset target=(?P<target>\d+) caller=(?P<caller>\S+)")
XENVBD_RE_DISABLE = re.compile(
    r"TDTEST BlkifRingDisable target=(?P<target>\d+) ring=(?P<ring>\d+).*?faked=(?P<faked>\d+)"
)
XENVBD_RE_FAKE = re.compile(
    r"TDTEST fake target=(?P<target>\d+) ring=(?P<ring>\d+) id=(?P<id>[0-9a-f]+)"
)
XENVBD_RE_UNKNOWN_ID = re.compile(
    r"TDTEST unknown-id target=(?P<target>\d+) ring=(?P<ring>\d+) id=(?P<id>[0-9a-f]+)"
)
XENVBD_RE_RING_CONNECT = re.compile(r"TDTEST ring-connect target=(?P<target>\d+) ring=(?P<ring>\d+)")
XENVBD_KV_RE = re.compile(r"(?P<k>[A-Za-z_][\w/-]*)=(?P<v>[^\s,][^\s]*)")
# Number bases of xenvbd fields: ids are hex (tapdisk's are decimal: normalise to int before correlating).
XENVBD_ID_BASE = 16
XENVBD_GREF_BASE = 10
# Events that describe one blkif request, and the suffixes of the lines that carry its grants.
XENVBD_REQUEST_EVENTS = frozenset({"submit", "defer", "rsp", "harvest", "fake", "deferred-fake"})
XENVBD_GREFS_SUFFIX = "-grefs"
XENVBD_IGREFS_SUFFIX = "-igrefs"


@dataclass
class XenvbdEvent:
    index: int
    line: str
    func: str
    event: str
    body: str
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
        return self.get_int("id", XENVBD_ID_BASE)

    @property
    def target(self) -> int | None:
        return self.get_int("target", 10)

    @property
    def ring(self) -> int | None:
        return self.get_int("ring", 10)

    @property
    def grefs(self) -> list[int]:
        return _gref_list(self.fields.get("grefs"))


def _gref_list(value: str | None) -> list[int]:
    out = []
    for part in (value or "").strip(",").split(","):
        try:
            out.append(int(part, XENVBD_GREF_BASE))
        except ValueError:
            pass
    return out


def parse_xenvbd_events(lines: Iterable[str]) -> list[XenvbdEvent]:
    """Parse every `xenvbd|<Function>: TDTEST <event> ...` line; `index` is the line's index in `lines`."""
    events = []
    for i, line in enumerate(lines):
        m = XENVBD_RE_ANY.search(line)
        if not m:
            continue
        body = m["body"] or ""
        fields = {kv["k"]: kv["v"] for kv in XENVBD_KV_RE.finditer(body)}
        events.append(XenvbdEvent(i, line, m["func"], m["event"], body, fields))
    return events


@dataclass
class XenvbdRequest:
    """One blkif request line (`event` is exactly submit, defer, rsp, harvest, fake or deferred-fake) and its grants."""

    event: str
    target: int | None
    ring: int | None
    id: int | None
    index: int
    op: str | None
    sector: int | None
    nsegs: int | None
    grefs: list[int] = field(default_factory=list)
    igrefs: list[int] = field(default_factory=list)
    line_indexes: list[int] = field(default_factory=list)


def group_xenvbd_requests(events: Iterable[XenvbdEvent]) -> list[XenvbdRequest]:
    """
    Attach each `<event>-grefs` / `<event>-igrefs` line to the preceding request line of the same event and
    (target, ring, id). Ids are normalised from hex to int. A grants line without a request line (the
    request line fell outside the log slice) gets a request of its own, with op=None.
    """
    requests: list[XenvbdRequest] = []
    open_requests: dict[tuple[str, int | None, int | None, int | None], XenvbdRequest] = {}
    for e in events:
        if e.event in XENVBD_REQUEST_EVENTS:
            req = XenvbdRequest(e.event, e.target, e.ring, e.id, e.index, e.fields.get("op"),
                                e.get_int("sector", 10), e.get_int("nsegs", 10), line_indexes=[e.index])
            requests.append(req)
            open_requests[(e.event, e.target, e.ring, e.id)] = req
            continue
        for suffix, attr in ((XENVBD_GREFS_SUFFIX, "grefs"), (XENVBD_IGREFS_SUFFIX, "igrefs")):
            if not e.event.endswith(suffix):
                continue
            base = e.event[:-len(suffix)]
            if base not in XENVBD_REQUEST_EVENTS:
                break
            key = (base, e.target, e.ring, e.id)
            req = open_requests.get(key)
            if req is None:
                req = XenvbdRequest(base, e.target, e.ring, e.id, e.index, None, None, None)
                requests.append(req)
                open_requests[key] = req
            getattr(req, attr).extend(e.grefs)
            req.line_indexes.append(e.index)
            break
    return requests


@dataclass
class XenvbdRingConnect:
    index: int
    target: int | None
    ring: int | None
    order: int | None
    grefs: list[int]
    line: str = ""


def xenvbd_ring_connects(events: Iterable[XenvbdEvent]) -> list[XenvbdRingConnect]:
    """`ring-connect` lines (first 8 shared-ring page grefs) merged with their `ring-connect-grefs` continuation."""
    out: list[XenvbdRingConnect] = []
    for e in events:
        if e.event == "ring-connect":
            out.append(XenvbdRingConnect(e.index, e.target, e.ring, e.get_int("order", 10), list(e.grefs), e.line))
        elif e.event == "ring-connect-grefs":
            for rc in reversed(out):
                if rc.target == e.target and rc.ring == e.ring:
                    rc.grefs.extend(e.grefs)
                    break
    return out


# --------------------------------------------------------------------------------------------------
# VM placement and power
# --------------------------------------------------------------------------------------------------

def assert_vm_on_host(vm: VM, host: Host) -> None:
    resident = vm.param_get("resident-on")
    assert resident == host.uuid, (
        f"test VM {vm.uuid} is resident on {resident}, not on the test host {host} ({host.uuid}); "
        "it is never migrated by these tests"
    )


def start_vm_on_host(vm: VM, host: Host) -> None:
    vm.start(on=host.uuid)
    wait_for_vm_running_and_ssh_up_without_tools(vm)
    assert_vm_on_host(vm, host)


def reboot_guest(vm: VM, host: Host, timeout_secs: int = 10 * 60) -> None:
    """Guest-initiated reboot: the domain is rebuilt in place on the same host with a new domid."""
    old_domid = vm.param_get("dom-id")
    try:
        vm.ssh("shutdown.exe -r -f -t 5")
    except commands.SSHCommandFailed as e:
        logging.info("reboot command returned %s (expected if the session dropped)", e.returncode)
    wait_for(lambda: vm.is_running() and vm.param_get("dom-id") not in (old_domid, "-1"),
             "Wait for the guest to reboot", timeout_secs=timeout_secs, retry_delay_secs=5)
    wait_for(vm.is_ssh_up, "Wait for SSH up", timeout_secs=timeout_secs)
    assert_vm_on_host(vm, host)


# --------------------------------------------------------------------------------------------------
# Driver install
# --------------------------------------------------------------------------------------------------

def sftp_put_file(vm: VM, local: Path, remote: str) -> None:
    """Upload with sftp, quoting both paths (remote paths look like /testdrivers/x for C:/testdrivers/x)."""
    assert vm.ip is not None
    commands.sftp(vm.ip, [f'put "{Path(local).as_posix()}" "{remote}"'])


def install_test_drivers(vm: VM, host: Host, zip_path: Path) -> PureWindowsPath:
    """
    Install a staged repro driver build (zip with <drv>/x64/<drv>.{inf,sys,cat} and testsign.crt).

    Returns the guest directory holding the expanded drivers.
    """
    assert zip_path.is_file(), f"{zip_path} not found"
    vm.execute_powershell_script(
        f"Remove-Item -Recurse -Force '{GUEST_DRIVER_DIR}' -ErrorAction SilentlyContinue; "
        f"New-Item -ItemType Directory -Force '{GUEST_DRIVER_DIR}' | Out-Null"
    )
    sftp_put_file(vm, zip_path, "/testdrivers/drivers.zip")
    sftp_put_file(vm, INSTALL_DRIVERS_PS1, "/testdrivers/install-drivers.ps1")
    cert = vm.execute_powershell_script(
        f"""Expand-Archive -Force -Path '{GUEST_DRIVER_DIR}\\drivers.zip' -DestinationPath '{GUEST_DRIVER_DIR}\\pkg';
$c = @(Get-ChildItem -Recurse -File -Filter testsign.crt '{GUEST_DRIVER_DIR}\\pkg');
if ($c.Count -ne 1) {{ throw "expected one testsign.crt in the zip, found $($c.Count)" }};
$root = $c[0].DirectoryName;
foreach ($d in @('xenbus', 'xeniface', 'xenvbd', 'xenvif', 'xennet')) {{
    if (-not (Test-Path "$root\\$d\\x64\\$d.inf")) {{ throw "missing $d\\x64\\$d.inf next to testsign.crt" }}
}};
$c[0].FullName"""
    ).strip()
    cert_path = PureWindowsPath(cert)
    driver_root = cert_path.parent
    logging.info("Test drivers expanded to %s", driver_root)

    logging.info("Enable testsigning with %s", cert_path)
    enable_testsign(vm, cert_path)
    assert_vm_on_host(vm, host)

    # install-drivers.ps1 is run unchanged. It runs in the background because installing xennet drops the
    # network, and so the SSH session, of the guest.
    logging.info("Install test drivers from %s", driver_root)
    vm.start_background_powershell(
        "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; "
        f"& '{GUEST_DRIVER_DIR}\\install-drivers.ps1' -DriverPath '{driver_root}' "
        f"*> '{GUEST_DRIVER_DIR}\\install.log'; "
        + WINDOWS_SHUTDOWN_COMMAND
    )
    wait_for(vm.is_halted, "Wait for the VM to shut down after the driver install", timeout_secs=300)
    start_vm_on_host(vm, host)
    install_log = vm.execute_powershell_script(
        f"Get-Content -Raw '{GUEST_DRIVER_DIR}\\install.log' -ErrorAction SilentlyContinue"
    )
    logging.info("install-drivers.ps1 output:\n%s", install_log)
    wait_for(vm.are_windows_tools_working, "Wait for the PV drivers to be detected", timeout_secs=180,
             retry_delay_secs=10)
    return driver_root


# --------------------------------------------------------------------------------------------------
# Registry knobs
# --------------------------------------------------------------------------------------------------

def check_driver_verifier_off(vm: VM) -> None:
    configured = vm.execute_powershell_script(
        f"""$p = Get-ItemProperty '{MEMORY_MANAGEMENT_KEY}';
[bool]$p.VerifyDrivers -or [bool]$p.VerifyDriverLevel"""
    )
    assert not strtobool(configured), "Driver Verifier is configured on the test VM; turn it off first"


def configure_repro_registry(vm: VM, host: Host, knobs: Mapping[str, int], *, target: int, max_queues: int = 1,
                             timeout_value: int = DISK_TIMEOUT_VALUE_S,
                             qemu_log_level: Iterable[str] = QEMU_LOG_LEVEL, reboot: bool = True) -> None:
    """
    Set the repro registry state, then reboot (the knobs are read at DriverEntry).

    Knobs absent from `knobs` are removed, so they fall back to their defaults.

    `target` is the xenvbd target id of the test disk (see parse_vbd_target()). `test-targets` is a bitmask of
    targets and gates every xenvbd test knob (close delay, defer-fake, disable wait, submit/rsp logging,
    ring-connect), so it is always written as `1 << target` and `knobs` must not say otherwise.
    """
    if not 0 <= target < 32:
        raise ValueError(f"invalid xenvbd target id {target!r}")
    want = 1 << target
    if knobs.get("test-targets", want) != want:
        raise ValueError(f"test-targets={knobs['test-targets']} does not address target {target}")
    knobs = {**knobs, "test-targets": want}
    unknown = set(knobs) - XENVBD_TEST_KNOBS
    if unknown:
        raise ValueError(f"unknown xenvbd knobs: {sorted(unknown)}")
    for name, value in knobs.items():
        if not isinstance(value, int) or value < 0 or value > 0xFFFFFFFF:
            raise ValueError(f"invalid DWORD for {name}: {value!r}")
    if knobs.get("test-close-delay-ms", 0) > XENVBD_CLOSE_DELAY_MAX_MS:
        raise ValueError(f"test-close-delay-ms is capped at {XENVBD_CLOSE_DELAY_MAX_MS}")
    if knobs.get("test-disable-wait-ms", 0) > XENVBD_DISABLE_WAIT_MAX_MS:
        raise ValueError(f"test-disable-wait-ms is capped at {XENVBD_DISABLE_WAIT_MAX_MS}")

    check_driver_verifier_off(vm)
    lines = [
        f"$p = '{XENVBD_PARAMETERS_KEY}'",
        "if (-not (Test-Path $p)) { New-Item -Path $p | Out-Null }",
    ]
    for name in sorted(XENVBD_TEST_KNOBS):
        if name in knobs:
            lines.append(f"Set-ItemProperty -Path $p -Name '{name}' -Type DWord -Value {int(knobs[name])}")
        else:
            lines.append(f"Remove-ItemProperty -Path $p -Name '{name}' -ErrorAction SilentlyContinue")
    lines += [
        f"Set-ItemProperty -Path $p -Name 'multi-queue-max-queues' -Type DWord -Value {int(max_queues)}",
        f"Set-ItemProperty -Path '{DISK_KEY}' -Name 'TimeOutValue' -Type DWord -Value {int(timeout_value)}",
        f"$x = '{XEN_PARAMETERS_KEY}'",
        "if (-not (Test-Path $x)) { New-Item -Path $x | Out-Null }",
        "Set-ItemProperty -Path $x -Name 'QemuLogLevel' -Type MultiString -Value @("
        + ",".join(f"'{level}'" for level in qemu_log_level) + ")",
        # Kernel memory dump, so that a bugcheck during the tests leaves something to look at.
        f"Set-ItemProperty -Path '{CRASH_CONTROL_KEY}' -Name 'CrashDumpEnabled' -Type DWord -Value 2",
    ]
    logging.info("Configure repro registry: %s", dict(knobs))
    vm.execute_powershell_script(";\n".join(lines))
    if reboot:
        reboot_guest(vm, host)


# --------------------------------------------------------------------------------------------------
# Disks and targets
# --------------------------------------------------------------------------------------------------

def list_disk_numbers(vm: VM) -> list[int]:
    out = vm.execute_powershell_script("Update-HostStorageCache; Get-Disk | ForEach-Object { $_.Number }")
    return [int(x) for x in out.split()]


def find_new_raw_disk(vm: VM, size_bytes: int, before: Iterable[int], timeout_secs: int = 120) -> int:
    """Find the single new, RAW, non-system disk of exactly size_bytes that appeared after `before`."""
    known = ",".join(str(n) for n in before) or "-1"
    script = f"""Update-HostStorageCache;
$known = @({known});
@(Get-Disk | Where-Object {{ $_.Size -eq {int(size_bytes)} -and $_.PartitionStyle -eq 'RAW' -and
    -not $_.IsSystem -and -not $_.IsBoot -and $known -notcontains $_.Number }}) | ForEach-Object {{ $_.Number }}"""

    def probe() -> list[int]:
        found = [int(x) for x in vm.execute_powershell_script(script).split()]
        if len(found) > 1:
            raise AssertionError(f"several new RAW disks of {size_bytes} bytes: {found}")
        return found

    return wait_for(probe, "Wait for the test disk to appear in the guest", timeout_secs=timeout_secs)[0]


def bring_disk_online(vm: VM, number: int) -> None:
    vm.execute_powershell_script(
        f"""$d = Get-Disk -Number {int(number)};
if ($d.IsSystem -or $d.IsBoot -or $d.PartitionStyle -ne 'RAW') {{ throw "refusing disk {int(number)}" }};
if ($d.IsOffline) {{ Set-Disk -Number {int(number)} -IsOffline $false }};
if ($d.IsReadOnly) {{ Set-Disk -Number {int(number)} -IsReadOnly $false }}"""
    )


def parse_vbd_target(devid: int) -> int | None:
    """Port of xenvbd's __ParseVbd(): xenstore vbd device id -> xenvbd target id."""
    if devid & (1 << 28):
        return (devid & ((1 << 20) - 1)) >> 8
    major = devid >> 8
    if major in (202, 8):
        return (devid & 0xF0) >> 4
    ide = {3: 0, 22: 2, 33: 4, 34: 6, 56: 8, 57: 10, 88: 12, 89: 14}
    if major in ide:
        return ((devid & 0xC0) >> 6) + ide[major]
    return None


def resolve_backend_devid(host: Host, domid: int, vdi_uuid: str) -> int | None:
    """Find the xenstore backend device id of the VBD whose backend `params` mention the VDI."""
    script = f"""for t in vbd3 vbd; do
  for d in $(xenstore-list /local/domain/0/backend/$t/{int(domid)} 2>/dev/null); do
    if xenstore-read /local/domain/0/backend/$t/{int(domid)}/$d/params 2>/dev/null | grep -qF '{vdi_uuid}'; then
      echo $d
    fi
  done
done"""
    found = host.ssh(script).split()
    if len(found) != 1:
        logging.warning("backend device id lookup for VDI %s on domain %s returned %s", vdi_uuid, domid, found)
        return None
    return int(found[0])


def pick_free_userdevice(vm: VM) -> str:
    allowed = [d for d in vm.param_get("allowed-VBD-devices").replace(";", " ").split() if d.isdigit()]
    candidates = sorted((int(d) for d in allowed if 1 <= int(d) < 16))
    assert candidates, "no free userdevice below 16 on the test VM"
    return str(candidates[0])


def wait_settle(seconds: float) -> None:
    logging.info("Wait %ss", seconds)
    time.sleep(seconds)
