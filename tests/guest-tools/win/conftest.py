import logging
from typing import Any
import pytest

from data import OTHER_GUEST_TOOLS, OTHER_GUEST_TOOLS_ISO, WIN_GUEST_TOOLS_ISOS
from lib.common import wait_for
from lib.host import Host
from lib.snapshot import Snapshot
from lib.sr import SR
from lib.vm import VM
from . import (
    WINDOWS_SHUTDOWN_COMMAND,
    PowerAction,
    iso_create,
    try_get_and_store_vm_ip_serial,
    wait_for_vm_running_and_ssh_up_without_tools,
)
from .guest_tools import install_guest_tools
from .other_tools import install_other_drivers


@pytest.fixture(scope="module")
def running_windows_vm_without_tools(imported_vm: VM) -> VM:
    vm = imported_vm
    if not vm.is_running():
        vm.start()
    wait_for(vm.is_running, "Wait for VM running")
    # Whenever the guest changes its serial port config, xl console will drop out.
    # Retry several times to force xl console to refresh.
    wait_for(lambda: try_get_and_store_vm_ip_serial(vm, timeout=10), "Wait for VM IP", 600)
    logging.info(f"VM IP: {vm.ip}")
    wait_for(vm.is_ssh_up, "Wait for VM SSH up")
    return vm
    # no teardown


@pytest.fixture(scope="module")
def unsealed_windows_vm_and_snapshot(running_windows_vm_without_tools: VM):
    """Unseal VM and get its IP, then shut it down. Cache the unsealed state in a snapshot to save time."""
    vm = running_windows_vm_without_tools
    # vm.shutdown is not usable yet (there's no tools).
    vm.ssh(WINDOWS_SHUTDOWN_COMMAND)
    wait_for(vm.is_halted, "Shutdown VM")
    snapshot = vm.snapshot()
    yield vm, snapshot
    snapshot.destroy(verify=True)


@pytest.fixture
def running_unsealed_windows_vm(unsealed_windows_vm_and_snapshot: tuple[VM, Snapshot]):
    vm, snapshot = unsealed_windows_vm_and_snapshot
    vm.start()
    wait_for_vm_running_and_ssh_up_without_tools(vm)
    yield vm
    snapshot.revert()


@pytest.fixture(scope="class")
def vm_install_test_tools_per_test_class(unsealed_windows_vm_and_snapshot, guest_tools_iso: dict[str, Any]):
    vm, snapshot = unsealed_windows_vm_and_snapshot
    vm.start()
    wait_for_vm_running_and_ssh_up_without_tools(vm)
    install_guest_tools(vm, guest_tools_iso, PowerAction.Reboot, check=False)
    yield vm
    snapshot.revert()


@pytest.fixture
def vm_install_test_tools_no_reboot(running_unsealed_windows_vm: VM, guest_tools_iso: dict[str, Any]):
    install_guest_tools(running_unsealed_windows_vm, guest_tools_iso, PowerAction.Nothing)
    return running_unsealed_windows_vm


@pytest.fixture(
    scope="module",
    ids=list(WIN_GUEST_TOOLS_ISOS.keys()),
    params=list(WIN_GUEST_TOOLS_ISOS.values()),
)
def guest_tools_iso(host: Host, request: pytest.FixtureRequest, nfs_iso_sr: SR):
    yield from iso_create(host, nfs_iso_sr, request.param)


@pytest.fixture(scope="module")
def other_tools_iso(host: Host, nfs_iso_sr: SR):
    yield from iso_create(host, nfs_iso_sr, OTHER_GUEST_TOOLS_ISO)


@pytest.fixture(ids=list(OTHER_GUEST_TOOLS.keys()), params=list(OTHER_GUEST_TOOLS.values()))
def vm_install_other_drivers(
    unsealed_windows_vm_and_snapshot: tuple[VM, Snapshot],
    other_tools_iso: dict[str, Any],
    request: pytest.FixtureRequest,
):
    vm, snapshot = unsealed_windows_vm_and_snapshot
    param = request.param
    install_other_drivers(vm, other_tools_iso["name"], param)
    yield vm, param
    snapshot.revert()
