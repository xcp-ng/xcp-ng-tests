import pytest

from lib.common import wait_for
from lib.host import Host
from lib.snapshot import Snapshot
from lib.sr import SR
from lib.vm import VM

from typing import Generator

# Requirements:
# From --hosts parameter:
# - host: XCP-ng host >= 8.3, in a pool with at least 2 hosts
# - a shared SR in the pool
# From --vm parameter:
# - A UEFI VM to import

pytestmark = pytest.mark.default_vm('mini-linux-x86_64-uefi')

# Variables that OVMF rewrites at every boot, whatever the host:
# - MemoryTypeInformation: holds memory usage statistics
# - MTC: monotonic boot counter
UEFI_VARS_CHANGED_AT_BOOT = {
    ('4c19049f-4137-4dd3-9c10-8b97a83ffdfa', 'MemoryTypeInformation'),
    ('eb704011-1402-11d3-8e77-00a0c969723b', 'MTC'),
}

def get_uefi_vars_checksums(vm: VM) -> dict[tuple[str, str], str]:
    """Return {(guid, name): sha256 of data} for every UEFI variable of the VM."""
    uefi_vars: dict[tuple[str, str], str] = {}
    for line in vm.host.ssh(f'varstore-ls {vm.uuid}').splitlines():
        guid, name = line.split(' ', 1)
        if (guid, name) in UEFI_VARS_CHANGED_AT_BOOT:
            continue
        checksum = vm.host.ssh(f'set -o pipefail; varstore-get {vm.uuid} {guid} {name} | sha256sum')
        # We use a checksum instead of raw data to keep dict small and we only need to know
        # if a variable changed. And we compute it on the host so only a short text comes
        # back and we avoid having garbage output when something goes wrong.
        uefi_vars[(guid, name)] = checksum.split()[0]
    return uefi_vars

def assert_same_uefi_vars(reference: dict[tuple[str, str], str], current: dict[tuple[str, str], str]) -> None:
    changed = sorted(f'{guid} {name}' for (guid, name) in reference.keys() & current.keys()
                     if reference[(guid, name)] != current[(guid, name)])
    missing = sorted(f'{guid} {name}' for (guid, name) in reference.keys() - current.keys())
    added = sorted(f'{guid} {name}' for (guid, name) in current.keys() - reference.keys())
    assert not (changed or missing or added), \
        f"UEFI variables differ: changed={changed}, missing={missing}, added={added}"

@pytest.fixture(scope='module')
def vm_on_shared_sr(uefi_vm: VM, shared_sr: SR) -> Generator[VM, None, None]:
    """The UEFI VM with its disks on the shared SR, so it can start on any host of the pool."""
    vm = uefi_vm
    if not vm.all_vdis_on_sr(shared_sr):
        vm.migrate(vm.host, shared_sr)
    yield vm

@pytest.fixture(scope='module')
def vm_snapshot(vm_on_shared_sr: VM) -> Generator[Snapshot, None, None]:
    snapshot = vm_on_shared_sr.snapshot()
    yield snapshot
    snapshot.destroy()

@pytest.fixture
def clean_vm(vm_on_shared_sr: VM, vm_snapshot: Snapshot) -> Generator[VM, None, None]:
    yield vm_on_shared_sr
    vm_snapshot.revert()

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
class TestUEFIVarsAfterHostChange:
    def test_cold_start_on_other_host(self, host: Host, hostA2: Host, clean_vm: VM) -> None:
        vm = clean_vm

        # Boot on the first host and record the variables
        vm.start(on=host.uuid)
        vm.wait_for_vm_running_and_ssh_up()
        reference = get_uefi_vars_checksums(vm)
        vm.shutdown(verify=True)

        # Cold start on the second host, then reboot there
        vm.start(on=hostA2.uuid)
        vm.wait_for_vm_running_and_ssh_up()
        assert vm.is_running_on_host(hostA2)

        assert_same_uefi_vars(reference, get_uefi_vars_checksums(vm))
        vm.reboot(verify=True)
        assert_same_uefi_vars(reference, get_uefi_vars_checksums(vm))

        # Back on the first host: the shared NVRAM must still be preserved
        vm.shutdown(verify=True)
        vm.start(on=host.uuid)
        vm.wait_for_vm_running_and_ssh_up()
        assert vm.is_running_on_host(host)

        assert_same_uefi_vars(reference, get_uefi_vars_checksums(vm))
        vm.reboot(verify=True)
        assert_same_uefi_vars(reference, get_uefi_vars_checksums(vm))

    def test_live_migration(self, host: Host, hostA2: Host, clean_vm: VM) -> None:
        vm = clean_vm

        # Boot on the first host and record the variables
        vm.start(on=host.uuid)
        vm.wait_for_vm_running_and_ssh_up()
        reference = get_uefi_vars_checksums(vm)

        # Live migrate to the second host, then reboot there
        vm.migrate(hostA2)
        # We use wait_for instead of assert to follow test_live_migrate from test_basic_without_ssh.py
        wait_for(lambda: vm.is_running_on_host(hostA2), "Wait for VM running on hostA2")

        assert_same_uefi_vars(reference, get_uefi_vars_checksums(vm))
        vm.reboot(verify=True)
        assert_same_uefi_vars(reference, get_uefi_vars_checksums(vm))
