from __future__ import annotations

import pytest

import logging
import random

from lib import config
from lib.blktap import TapCtl, VBDConnector, XenStoreHelper
from lib.common import MiB
from lib.host import Host
from lib.vm import VM
from tests.storage import install_randstream
from tests.storage.blktap import (
    ConnectedVBD,
    assert_tapdisk_destroyed,
    assert_tapdisk_io,
    wait_for_guest_device,
    write_then_read,
)
from tests.storage.storage import StreamSpan, compute_span_layout

# VM I/O through tapdisk, and its tapback integration
#
# Requirements:
# - an XCP-ng host with blktap and tapback
# - a small unix VM with dd and blockdev in the guest, and internet access (to install randstream)

@pytest.mark.small_vm
@pytest.mark.unix_vm
@pytest.mark.parametrize('image', ['vhd', 'qcow2'], indirect=True)
class TestVMBasicIO:
    @pytest.mark.parametrize('iterations, size_mib', [(1, 10), (3, 5)], ids=['1x10MiB', '3x5MiB'])
    def test_vm_io(self, host: Host, running_vm: VM, image: str, vbd_connector: VBDConnector,
                   iterations: int, size_mib: int) -> None:
        """VM I/O on the device, repeated or not, all go through tapdisk."""
        vm = running_vm
        device = "xvdb"
        pid, minor = vbd_connector.connect(vm, image, device)
        wait_for_guest_device(vm, device)

        for i in range(iterations):
            logging.info(f"I/O iteration {i + 1}/{iterations}")
            write_then_read(vm, device, size_mib=size_mib)
        assert_tapdisk_io(host, pid, minor, size_mib=iterations * size_mib)

    def test_vm_device_appears_in_guest(self, running_vm: VM, image: str, vbd_connector: VBDConnector) -> None:
        """The device appears in the guest with the size of the image."""
        vm = running_vm
        device = "xvdc"
        vbd_connector.connect(vm, image, device)
        wait_for_guest_device(vm, device)

        size = int(vm.ssh(f'blockdev --getsize64 /dev/{device}'))
        assert size == 100 * MiB, \
            f"Device size is {size} bytes, expected 100 MiB (size of the image)"

@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestVMConnectDisconnect:
    @pytest.mark.parametrize('image', ['vhd', 'qcow2'], indirect=True)
    def test_connect_disconnect_cycle(self, host: Host, running_vm: VM, image: str,
                                      vbd_connector: VBDConnector) -> None:
        """The same image can be connected and disconnected several times in a row."""
        vm = running_vm
        device = "xvde"
        for cycle in range(2):
            logging.info(f"Connection cycle {cycle + 1}/2")
            pid, minor = vbd_connector.connect(vm, image, device)
            wait_for_guest_device(vm, device)
            vm.ssh(f'dd if=/dev/urandom of=/dev/{device} bs=1M count=1 conv=fsync status=none')

            vbd_connector.disconnect(vm, device)
            wait_for_guest_device(vm, device, present=False)
            assert_tapdisk_destroyed(host, pid, minor)

@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestTapbackIntegration:
    @pytest.mark.parametrize('image', ['vhd', 'qcow2'], indirect=True)
    def test_connected_vbd_has_kthread(self, connected_vbd: ConnectedVBD, vbd_connector: VBDConnector,
                                       xenstore: XenStoreHelper) -> None:
        """Tapback publishes in kthread-pid the pid of the tapdisk serving the VBD."""
        backend = vbd_connector.backend_path(connected_vbd.vm, connected_vbd.device)
        kthread_pid = xenstore.read(f"{backend}/kthread-pid")
        assert kthread_pid == str(connected_vbd.pid), \
            f"kthread-pid is {kthread_pid}, expected {connected_vbd.pid} (pid of the tapdisk)"

@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestDataIntegrity:
    @pytest.mark.parametrize("reopen", [False, True], ids=["pause", "pause-reopen"])
    def test_data_integrity_across_pause(self, host: Host, connected_vbd: ConnectedVBD, reopen: bool) -> None:
        """Random data written before a tapdisk pause/unpause must be read back unchanged."""
        vm = connected_vbd.vm
        dev = f"/dev/{connected_vbd.device}"
        # log the seed so that a failure can be reproduced
        seed = random.randrange(2**32)
        logging.info(f"Random seed: {seed}")

        install_randstream(vm)
        dev_size = int(vm.ssh(f'blockdev --getsize64 {dev}'))
        # write at the start, in the middle and at the end of the device
        layout = compute_span_layout(dev_size, dev_size // 2, 3, config.write_volume_align)
        spans = [StreamSpan(position=position, size=size) for position, size in layout]
        for i, span in enumerate(spans):
            span.generate(vm, dev, seed=seed + i)
        # make sure the data reached tapdisk, and is not read back from the guest page cache
        vm.ssh(f'blockdev --flushbufs {dev}')

        tapctl = TapCtl(host)
        tapctl.pause(connected_vbd.pid, connected_vbd.minor)
        # unpausing with the image path makes tapdisk close and reopen it, like SM does
        tapctl.unpause(connected_vbd.pid, connected_vbd.minor, connected_vbd.image if reopen else None)

        for span in spans:
            span.validate(vm, dev)
