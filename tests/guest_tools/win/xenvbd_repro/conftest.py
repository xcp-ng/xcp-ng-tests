"""
Fixtures for the xenvbd/tapdisk shutdown-ordering repro (S-0 smoke tests, S-A, S-B, S-C, S-D).

HOST CHANGES - read before running. The user confirms these out of band; nothing here prompts:
- /root/tdtest/ on the test host receives tapdisk.tdtest, tdtest-ctl, a stock copy of /usr/libexec/tapdisk
  (tapdisk.stock, saved only while `rpm -V blktap` reports it unmodified, with tapdisk.stock.sha256) and
  baseline-*.json records (rpm -q/-V blktap, sha256, XCP-ng version, uname -r) taken before the first swap.
- /run/tapdisk-test/<minor>.conf is written for the test tapdisk's minor only, and removed at teardown.
- /usr/libexec/tapdisk is swapped for the tdtest build only for the few seconds of each test VBD plug,
  then restored from tapdisk.stock (cp -p) and its sha256 checked. Rollback if anything is ever left:
  `cp -p /root/tdtest/tapdisk.stock /usr/libexec/tapdisk` or `yum reinstall blktap`.
- tapback is never touched. Only the tapdisk serving the test VDI is ever signalled (see lib/tapdisk_test.py).

Guest/VM: only the --vm test VM is used; it is never migrated; a pre-test snapshot is reverted at the end
of the module. Test VDIs are only created on --repro-sr.
"""
from __future__ import annotations

import pytest

import contextlib
import logging
import time
from pathlib import Path

from lib.common import is_uuid, randid, strtobool
from lib.host import Host
from lib.sr import SR
from lib.tapdisk_test import (
    LogSlice,
    TapdiskRestoreError,
    TdtestDeployment,
    TdtestSafetyError,
    assert_patched,
    check_stock_restored,
    deploy_tdtest,
    find_tapdisk,
    patched_tapdisk_pids,
    remove_tdtest_conf,
    swap_window,
    tdtest_off,
)
from lib.vbd import VBD
from lib.vm import VM
from lib.windows import vm_shutdown_without_tools
from lib.windows.blockstamp import Blockstamp
from lib.windows.test_drivers import (
    XENVBD_RE_RING_CONNECT,
    assert_vm_on_host,
    bring_disk_online,
    configure_repro_registry,
    find_new_raw_disk,
    install_test_drivers,
    list_disk_numbers,
    parse_vbd_target,
    pick_free_userdevice,
    resolve_backend_devid,
    start_vm_on_host,
)
from tests.guest_tools.win.xenvbd_repro import (
    EXPECT_CHOICES,
    REPRO_DISK_SIZE,
    RESET_CHOICES,
    XENVBD_LOG_WINDOW,
    ReproContext,
    ReproDisk,
    ReproHost,
    ReproOptions,
    ReproVM,
    Variant,
    check_banner,
    find_banner,
)

from typing import Generator

PACKAGE_DIR = Path(__file__).parent
REQUIRED_OPTIONS = ("--repro-drivers", "--repro-sr", "--repro-tapdisk", "--repro-tdtest-ctl")
REPRO_VDI_LABEL = "xenvbd-repro"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("xenvbd-repro", "xenvbd shutdown-ordering repro (tests/guest_tools/win/xenvbd_repro)")
    group.addoption("--repro-drivers", default=None,
                    help="Local zip of a staged, test-signed repro driver build (<drv>/x64/* and testsign.crt)")
    group.addoption("--repro-sr", default=None,
                    help="UUID of the SR for test VDIs (local to the test host, or attached there)")
    group.addoption("--repro-tapdisk", default=None, help="Local path of the tdtest-patched tapdisk binary")
    group.addoption("--repro-tdtest-ctl", default=None, help="Local path of the tdtest-ctl binary")
    group.addoption("--repro-expect", default="reproduce", choices=EXPECT_CHOICES,
                    help="reproduce: assert the hazard is shown; clean: assert it is not; observe: record only")
    group.addoption("--repro-variant", default="D0",
                    help="Driver build and remediations under test: <build>[+R1][+R2][+R3], e.g. D0+R2+R3")
    group.addoption("--repro-reset", default="timeout", choices=RESET_CHOICES,
                    help="How S-A triggers the StorPort reset: disk TimeOutValue expiry, or IOCTL_STORAGE_RESET_DEVICE")
    group.addoption("--repro-artifacts", default="repro-artifacts",
                    help="Directory for log slices, guest JSON/ledgers and evidence (a timestamped subdir per session)")
    group.addoption("--repro-run-shutdown", action="store_true", default=False,
                    help="Also run the optional, observation-only S-D shutdown-with-held-read test")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Skip at collection time (before any fixture, so no VM is touched) when not configured for the repro.
    missing = [opt for opt in REQUIRED_OPTIONS if not config.getoption(opt, default=None)]
    try:
        legacy = Variant.parse(config.getoption("--repro-variant", default="D0") or "D0").legacy
    except ValueError:
        legacy = False # reported by the repro_options fixture
    for item in items:
        if PACKAGE_DIR not in item.path.parents:
            continue
        if missing:
            item.add_marker(pytest.mark.skip(reason=f"xenvbd repro not configured: missing {', '.join(missing)}"))
        elif "test_sD_" in item.name and not config.getoption("--repro-run-shutdown"):
            item.add_marker(pytest.mark.skip(reason="optional S-D test: pass --repro-run-shutdown"))
        elif "test_sA_legacy_positive_control" in item.name and not legacy:
            item.add_marker(pytest.mark.skip(reason="needs the D0-legacy drivers (--repro-variant=D0-legacy)"))
        elif "test_sA_abandoned_read" in item.name and legacy:
            item.add_marker(pytest.mark.skip(reason="legacy drivers: run test_sA_legacy_positive_control instead"))


# --------------------------------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------------------------------

@pytest.fixture(scope="session")
def repro_options(pytestconfig: pytest.Config) -> ReproOptions:
    def path_opt(name: str) -> Path:
        value = str(pytestconfig.getoption(name) or "")
        if not value:
            pytest.skip(f"{name} is required")
        p = Path(value)
        if not p.is_file():
            pytest.fail(f"{name}: {p} does not exist", pytrace=False)
        return p

    sr_uuid = str(pytestconfig.getoption("--repro-sr") or "")
    if not is_uuid(sr_uuid):
        pytest.fail("--repro-sr must be an SR UUID", pytrace=False)
    try:
        variant = Variant.parse(str(pytestconfig.getoption("--repro-variant")))
    except ValueError as e:
        pytest.fail(f"--repro-variant: {e}", pytrace=False)
    artifacts = Path(str(pytestconfig.getoption("--repro-artifacts"))) / time.strftime("%Y%m%d-%H%M%S")
    artifacts.mkdir(parents=True, exist_ok=True)
    return ReproOptions(
        drivers=path_opt("--repro-drivers"),
        sr_uuid=sr_uuid.lower(),
        tapdisk=path_opt("--repro-tapdisk"),
        tdtest_ctl=path_opt("--repro-tdtest-ctl"),
        expect=str(pytestconfig.getoption("--repro-expect")),
        variant=variant,
        reset=str(pytestconfig.getoption("--repro-reset")),
        artifacts=artifacts,
        run_shutdown=bool(pytestconfig.getoption("--repro-run-shutdown")),
    )


@pytest.fixture(scope="session")
def test_host_guard(host: Host, pytestconfig: pytest.Config, repro_options: ReproOptions) -> ReproHost:
    """Check that the test VM and the test SR are on the test host. Skips otherwise; never migrates."""
    vms: list[str] = list(pytestconfig.getoption("vm") or [])
    if len(vms) != 1 or not is_uuid(vms[0]):
        pytest.skip("the xenvbd repro needs exactly one --vm=<UUID of the designated Windows test VM>")
    vm = VM(vms[0], host)
    if not vm.is_windows:
        pytest.skip(f"VM {vm.uuid} is not a Windows VM")

    sr = SR(repro_options.sr_uuid, host.pool)
    pbds = host.xe("pbd-list", {"sr-uuid": sr.uuid, "host-uuid": host.uuid}, minimal=True)
    if not pbds:
        pytest.skip(f"SR {sr.uuid} has no PBD on the test host {host}: its tapdisks would not run there")
    if not strtobool(host.xe("pbd-param-get", {"uuid": pbds.split(",")[0], "param-name": "currently-attached"})):
        pytest.skip(f"the PBD of SR {sr.uuid} on {host} is not attached")
    if not sr.is_shared() and sr.hosts_uuids() != [host.uuid]:
        pytest.skip(f"local SR {sr.uuid} does not belong to the test host {host}")

    power = vm.power_state()
    if power == "running":
        if vm.param_get("resident-on") != host.uuid:
            pytest.skip(f"test VM {vm.uuid} runs on another host; move it to {host} by hand (never migrated here)")
    elif power == "halted":
        logging.info("Starting the test VM on the test host %s", host)
        vm.start(on=host.uuid)
    else:
        pytest.skip(f"test VM {vm.uuid} is {power}")

    leftovers = [
        vdi for vdi in vm.vdi_uuids()
        if REPRO_VDI_LABEL in (host.xe("vdi-param-get", {"uuid": vdi, "param-name": "name-label"}) or "")
    ]
    if leftovers:
        pytest.fail(f"test VM still has repro VDIs from an earlier run: {leftovers}; clean them up by hand",
                    pytrace=False)
    if len(host.pool.hosts) > 1:
        logging.warning("multi-host pool: the test VM is only ever started with on=%s", host.uuid)
    return ReproHost(host, vm.uuid, sr.uuid)


@pytest.fixture(scope="session")
def tdtest_deployed(test_host_guard: ReproHost, repro_options: ReproOptions) -> Generator[TdtestDeployment, None, None]:
    """Upload the tdtest artifacts once, after recording the host baseline and securing a stock copy."""
    host = test_host_guard.host
    try:
        deployment = deploy_tdtest(host, repro_options.tapdisk, repro_options.tdtest_ctl)
    except TdtestSafetyError as e:
        pytest.exit(f"STOP: {e}", returncode=3)
    deployment.save(repro_options.artifacts / "tdtest-deployment.json")
    yield deployment
    try:
        check_stock_restored(host, deployment)
        deployment.baseline["rpm_V_blktap_after"] = host.ssh("rpm -V blktap || true")
        leftover = patched_tapdisk_pids(host, deployment)
        deployment.baseline["patched_tapdisks_after"] = " ".join(str(p) for p in leftover)
        if leftover:
            logging.warning("tapdisks still running the tdtest binary (not killed): %s", leftover)
    finally:
        deployment.save(repro_options.artifacts / "tdtest-deployment.json")


# --------------------------------------------------------------------------------------------------
# Module: the VM with the repro drivers
# --------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def vm_with_test_drivers(
    test_host_guard: ReproHost, repro_options: ReproOptions, running_windows_vm_without_tools: VM
) -> Generator[ReproVM, None, None]:
    """Snapshot the VM (pre-test), install the repro drivers; revert and destroy the snapshot at teardown."""
    host = test_host_guard.host
    vm = running_windows_vm_without_tools
    assert vm.uuid == test_host_guard.vm_uuid
    assert_vm_on_host(vm, host)
    vm_shutdown_without_tools(vm)
    snapshot = vm.snapshot(name="xenvbd-repro pre-test")
    try:
        start_vm_on_host(vm, host)
        install_test_drivers(vm, host, repro_options.drivers)
        userdevice = pick_free_userdevice(vm)
        # Target id discovery: HVM xvd device ids are (202 << 8) | (userdevice << 4) for userdevice < 16, and
        # xenvbd's __ParseVbd() maps that to target = (devid & 0xF0) >> 4 = the userdevice. So the target is the
        # userdevice, and `test-targets` is set to 1 << target. repro_vdi checks this twice after each plug:
        # against the xenstore backend device id (parse_vbd_target), and against the target= of the xenvbd
        # `TDTEST ring-connect` lines, which are only logged for targets addressed by test-targets.
        target = int(userdevice)
        yield ReproVM(vm, host, snapshot, userdevice, target)
    finally:
        snapshot.revert()
        snapshot.destroy(verify=True)


@pytest.fixture
def repro_guest(request: pytest.FixtureRequest, vm_with_test_drivers: ReproVM,
                repro_options: ReproOptions) -> ReproVM:
    """Apply the registry knobs this test class needs (reboot only if they changed); before any test VBD."""
    rv = vm_with_test_drivers
    vm, host = rv.vm, rv.host
    if not vm.is_running():
        start_vm_on_host(vm, host)
    assert_vm_on_host(vm, host)

    knobs = {
        "test-log": 1,
        "test-log-sector-start": XENVBD_LOG_WINDOW.sector_start,
        "test-log-sector-end": XENVBD_LOG_WINDOW.sector_end,
        "test-targets": 1 << rv.target,
    }
    knobs.update(getattr(request.cls, "XENVBD_KNOBS", None) or {})
    knobs.update(repro_options.variant.xenvbd_knobs())
    if rv.applied_knobs != knobs:
        with LogSlice(host, f"boot-{request.node.name}", domid_fn=lambda: vm.param_get("dom-id"),
                      out_dirs=[repro_options.artifacts]) as boot:
            configure_repro_registry(vm, host, knobs, target=rv.target)
        rv.applied_knobs = knobs
        rv.banner = find_banner(boot.lines) or rv.banner
        logging.info("xenvbd banner: %s", rv.banner)
        check_banner(rv.banner, repro_options.variant)
    if not rv.script_pushed:
        Blockstamp.push(vm)
        rv.script_pushed = True
    return rv


# --------------------------------------------------------------------------------------------------
# Function: the test VDI and per-test tooling
# --------------------------------------------------------------------------------------------------

def _unplug_if_attached(vbd: VBD) -> None:
    if strtobool(vbd.param_get("currently-attached")):
        vbd.unplug()


def _check_ring_connect_target(plug_log: LogSlice, target: int) -> None:
    """xenvbd logs `TDTEST ring-connect target=<t>` only for targets in test-targets: expect ours, nothing else."""
    try:
        plug_log.wait_for(XENVBD_RE_RING_CONNECT, timeout=60, what="xenvbd TDTEST ring-connect of the test VBD")
    except TimeoutError:
        pytest.fail(
            f"no xenvbd TDTEST ring-connect line for target {target} after the plug: test-targets does not "
            "address the test disk, the driver is not a repro build, or the guest log does not reach the host",
            pytrace=False,
        )
    seen = {int(m["target"]) for m in plug_log.grep(XENVBD_RE_RING_CONNECT)}
    assert seen == {target}, f"xenvbd ring-connect lines for targets {sorted(seen)}, expected only {target}"


@pytest.fixture
def repro_vdi(repro_guest: ReproVM, test_host_guard: ReproHost, tdtest_deployed: TdtestDeployment,
              repro_options: ReproOptions) -> Generator[ReproDisk, None, None]:
    """A 2 GiB test VDI on --repro-sr, plugged inside a swap window so that its tapdisk runs tdtest."""
    host, vm = test_host_guard.host, repro_guest.vm
    sr = SR(test_host_guard.sr_uuid, host.pool)
    with contextlib.ExitStack() as stack:
        vdi = sr.create_vdi(REPRO_VDI_LABEL, virtual_size=REPRO_DISK_SIZE)
        stack.callback(vdi.destroy)
        vbd = vm.create_vbd(repro_guest.userdevice, vdi.uuid)
        stack.callback(vbd.destroy)
        before = list_disk_numbers(vm)
        stack.callback(_unplug_if_attached, vbd)
        with LogSlice(host, f"plug-{vdi.uuid}", domid_fn=lambda: vm.param_get("dom-id"),
                      out_dirs=[repro_options.artifacts]) as plug_log:
            try:
                with swap_window(host, tdtest_deployed, label=f"plug {vdi.uuid}"):
                    vbd.plug()
            except TapdiskRestoreError as e:
                pytest.exit(str(e), returncode=3)
            info = find_tapdisk(host, vdi.uuid)
            plug_log.add_pid(info.pid)
            _check_ring_connect_target(plug_log, repro_guest.target)
        stack.callback(remove_tdtest_conf, host, info.minor, vdi.uuid)
        stack.callback(tdtest_off, host, vdi.uuid)
        assert_patched(host, info.pid, tdtest_deployed)
        logging.info("test tapdisk pid=%d minor=%d path=%s", info.pid, info.minor, info.path)

        number = find_new_raw_disk(vm, REPRO_DISK_SIZE, before)
        bring_disk_online(vm, number)
        devid = resolve_backend_devid(host, int(vm.param_get("dom-id")), vdi.uuid)
        if devid is not None:
            target = parse_vbd_target(devid)
            assert target == repro_guest.target, (
                f"test VBD devid {devid} maps to xenvbd target {target}, but the knobs address "
                f"target {repro_guest.target}"
            )
        yield ReproDisk(vdi, vbd, number, info.pid, info.minor, repro_guest.target, devid)


@pytest.fixture
def repro_artifacts(request: pytest.FixtureRequest, repro_options: ReproOptions) -> Path:
    name = "".join(c if c.isalnum() or c in "-_." else "_" for c in request.node.name)[:100]
    d = repro_options.artifacts / f"{time.strftime('%H%M%S')}-{name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def repro(request: pytest.FixtureRequest, repro_vdi: ReproDisk, repro_guest: ReproVM,
          test_host_guard: ReproHost, tdtest_deployed: TdtestDeployment, repro_options: ReproOptions,
          repro_artifacts: Path, tmp_path: Path) -> Generator[ReproContext, None, None]:
    """Everything a scenario needs, with a host log slice open for the whole test."""
    host, vm = test_host_guard.host, repro_guest.vm
    run_id = f"{request.node.name[:40]}-{randid()}".replace("[", "-").replace("]", "")
    run_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in run_id)
    bs = Blockstamp(vm, repro_vdi.number, run_id, REPRO_DISK_SIZE, repro_artifacts)
    log = LogSlice(host, request.node.nodeid, domid_fn=lambda: vm.param_get("dom-id"), pids=[repro_vdi.pid],
                   out_dirs=[repro_artifacts, tmp_path])
    with log:
        yield ReproContext(host, vm, repro_vdi, bs, log, repro_options, tdtest_deployed, repro_guest,
                           repro_artifacts, request.node.name)
        try:
            if vm.is_running() and vm.is_ssh_up():
                bs.fetch_artifacts()
        except Exception as e:
            logging.warning("could not fetch the guest artifacts: %s", e)
