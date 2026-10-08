from __future__ import annotations

import pytest

import logging
import re

from lib.blktap import TapCtl, VBDConnector
from lib.common import exec_nofail, raise_errors
from lib.host import Host
from lib.vm import VM
from tests.storage.blktap import assert_tapdisk_destroyed, wait_for_guest_device, wait_for_tapdisk_exit

from typing import Generator

# A single tapdisk serving several VBDs: many VBDs without VM (equivalent to
# test-tapdisk-stress.sh from blktap-tools), and one read-only VBD shared by two VMs
# (equivalent to test-shared-readonly.sh)
#
# Requirements:
# - an XCP-ng host with blktap and tapback
# - a small unix VM with dd, blockdev and md5sum in the guest
# - enough space to import 2 VMs on default SR

VBD_COUNT = 50

@pytest.fixture(scope='module')
def two_vms(imported_vm: VM) -> Generator[tuple[VM, VM], None, None]:
    vm1 = imported_vm
    vm2 = vm1.clone()
    yield (vm1, vm2)
    # teardown
    errors = []
    logging.info("< Destroy VM2")
    errors += exec_nofail(lambda: vm2.destroy())
    raise_errors(errors)

@pytest.fixture
def small_vhd_paths(host: Host) -> Generator[list[str], None, None]:
    """VBD_COUNT 2 MiB VHDs in a dom0 temporary directory."""
    directory = host.ssh('mktemp -d /tmp/blktap-test-XXXXXX')
    host.ssh(f'for i in $(seq {VBD_COUNT}); do vhd-util create -n {directory}/disk$i.vhd -s 2; done')
    yield [f"{directory}/disk{i}.vhd" for i in range(1, VBD_COUNT + 1)]
    host.ssh(f'rm -rf {directory}')

@pytest.mark.no_vm
def test_many_vbds_on_one_tapdisk(host: Host, tapctl: TapCtl, small_vhd_paths: list[str]) -> None:
    """A single tapdisk serves many VBDs, each with its own image, then releases them all."""
    pid = tapctl.spawn()
    images = {}  # minor -> image path
    for path in small_vhd_paths:
        minor, _ = tapctl.allocate()
        tapctl.attach(pid, minor)
        tapctl.open(pid, minor, f"vhd:{path}")
        images[minor] = path

    listed = {t.minor: t.path for t in tapctl.list() if t.pid == pid}
    assert listed == images, \
        "tap-ctl list doesn't show the expected minors and images"
    # tapdisk finds the VBD of each minor
    for minor, path in images.items():
        name = tapctl.stats(pid, minor)['name']
        assert name == f"vhd:{path}", \
            f"minor {minor} serves {name}, expected vhd:{path}"

    for minor in images:
        tapctl.close(pid, minor)
        tapctl.detach(pid, minor)
    wait_for_tapdisk_exit(host, pid)
    for minor in images:
        tapctl.free(minor)
    # tap-ctl list shows the allocated minors without tapdisk as "minor=N" lines
    left = {int(m) for m in re.findall(r'minor=(\d+)', host.ssh('tap-ctl list'))} & images.keys()
    assert not left, \
        f"minors still allocated after free: {sorted(left)}"

@pytest.mark.small_vm
@pytest.mark.unix_vm
def test_readonly_vbd_shared_by_two_vms(host: Host, two_vms: tuple[VM, VM], vhd_path: str, tapctl: TapCtl,
                                        vbd_connector: VBDConnector) -> None:
    """A single read-only tapdisk serves the same image to two VMs, which both read its data."""
    vm1, vm2 = two_vms
    device = "xvdb"
    read_checksum = f'dd if=/dev/{device} bs=1M count=10 status=none | md5sum'
    for vm in (vm1, vm2):
        vm.start()
    for vm in (vm1, vm2):
        vm.wait_for_vm_running_and_ssh_up()

    # Write random data in the image through vm1, with a regular read-write VBD,
    # so that the VMs have known data to read afterwards
    vbd_connector.connect(vm1, f"vhd:{vhd_path}", device)
    wait_for_guest_device(vm1, device)
    vm1.ssh(f'dd if=/dev/urandom of=/dev/{device} bs=1M count=10 conv=fsync status=none')
    expected = vm1.ssh(read_checksum)
    vbd_connector.disconnect(vm1, device)
    wait_for_guest_device(vm1, device, present=False)

    # Open the image read-only in a single tapdisk, and attach this same tapdisk to both VMs
    pid, minor = tapctl.create(f"vhd:{vhd_path}", readonly=True)
    vbd_connector.attach(vm1, pid, minor, device, readonly=True)
    vbd_connector.attach(vm2, pid, minor, device, readonly=True)

    # Both VMs see a read-only device, with the data written by vm1
    for vm in (vm1, vm2):
        wait_for_guest_device(vm, device)
        assert vm.ssh(f'blockdev --getro /dev/{device}') == "1", \
            f"/dev/{device} is not read-only in VM {vm.uuid}"
        assert vm.ssh(read_checksum) == expected, \
            f"VM {vm.uuid} doesn't read the data of the image"

    # Detach the tapdisk from both VMs, then destroy it: it must release its minor
    for vm in (vm1, vm2):
        vbd_connector.detach(vm, device)
        wait_for_guest_device(vm, device, present=False)
    tapctl.close(pid, minor)
    tapctl.detach(pid, minor)
    wait_for_tapdisk_exit(host, pid)
    tapctl.free(minor)
    assert_tapdisk_destroyed(host, pid, minor)
