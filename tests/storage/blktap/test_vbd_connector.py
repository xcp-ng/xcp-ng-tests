from __future__ import annotations

import pytest

from lib.blktap import VBDConnector
from lib.host import Host
from lib.vm import VM
from tests.storage.blktap import (
    assert_tapdisk_destroyed,
    assert_tapdisk_io,
    wait_for_guest_device,
    write_concurrently,
    write_then_read,
)

# Requirements:
# - an XCP-ng host with blktap and tapback
# - a small unix VM with dd and blockdev in the guest

@pytest.mark.small_vm
@pytest.mark.unix_vm
@pytest.mark.parametrize('image_type', ['vhd', 'qcow2'])
def test_connect_vbd_to_running_vm(host: Host, running_vm: VM, vbd_connector: VBDConnector,
                                   image_type: str, request: pytest.FixtureRequest) -> None:
    """Connect an image to a running VM, do I/O on it, then disconnect it."""
    vm = running_vm
    image = f"{image_type}:{request.getfixturevalue(f'{image_type}_path')}"
    device = "xvdb"

    pid, minor = vbd_connector.connect(vm, image, device)
    wait_for_guest_device(vm, device)

    write_then_read(vm, device, size_mib=10)
    assert_tapdisk_io(host, pid, minor, size_mib=10)

    vbd_connector.disconnect(vm, device)
    wait_for_guest_device(vm, device, present=False)
    assert_tapdisk_destroyed(host, pid, minor)

@pytest.mark.small_vm
@pytest.mark.unix_vm
def test_connect_multiple_vbds(host: Host, running_vm: VM, vhd_paths: list[str],
                               vbd_connector: VBDConnector) -> None:
    """Connect several images to a VM, write to all of them at the same time, then disconnect them."""
    vm = running_vm
    devices = ["xvdb", "xvdc", "xvdd"]

    tapdisks = [vbd_connector.connect(vm, f"vhd:{path}", device) for path, device in zip(vhd_paths, devices)]
    for device in devices:
        wait_for_guest_device(vm, device)

    write_concurrently(vm, devices, size_mib=5)

    for device in devices:
        vbd_connector.disconnect(vm, device)
    for pid, minor in tapdisks:
        assert_tapdisk_destroyed(host, pid, minor)

# TODO: the write is refused by the guest kernel (mode=r) and never reaches tapdisk.
# Ideas to test tapdisk itself:
# - open it read-only (TapCtl.create(readonly=True)) but attach it with mode=w,
#   check that the guest sees a writable device (blockdev --getro), then that a write fails
# - check that the image sha256sum in dom0 is unchanged after the write attempt
# - check tapdisk's "bad td request ... (ro ...)" error in the host logs
@pytest.mark.small_vm
@pytest.mark.unix_vm
def test_connect_vbd_readonly(host: Host, running_vm: VM, vhd_path: str, vbd_connector: VBDConnector) -> None:
    """A read-only VBD can be read, but not written to."""
    vm = running_vm
    device = "xvdb"

    pid, minor = vbd_connector.connect(vm, f"vhd:{vhd_path}", device, readonly=True)
    wait_for_guest_device(vm, device)

    vm.ssh(f'dd if=/dev/{device} of=/dev/null bs=1M count=5 status=none')
    # The guest must see a read-only device, and writing to it must fail
    # (conv=fsync, so that a write accepted by the page cache still fails)
    assert vm.ssh(f'blockdev --getro /dev/{device}') == "1", \
        f"/dev/{device} is not read-only in the guest"
    result = vm.ssh_with_result(f'dd if=/dev/zero of=/dev/{device} bs=1M count=1 conv=fsync status=none')
    assert result.returncode != 0, \
        "Write succeeded on a read-only device"

    vbd_connector.disconnect(vm, device)
    assert_tapdisk_destroyed(host, pid, minor)
