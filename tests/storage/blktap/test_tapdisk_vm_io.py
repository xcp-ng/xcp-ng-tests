from __future__ import annotations

import pytest

import logging
import random
import time

from lib import config
from lib.blktap import TapCtl, VBDConnector, XenStoreHelper
from lib.common import Defer, MiB, wait_for
from lib.host import Host
from lib.vm import VM
from tests.storage import install_randstream
from tests.storage.blktap import (
    ConnectedVBD,
    assert_tapdisk_destroyed,
    assert_tapdisk_io,
    start_fio,
    wait_for_guest_device,
    wait_for_guest_reads,
    write_then_read,
)
from tests.storage.storage import StreamSpan, compute_span_layout

# VM I/O through tapdisk, and its tapback integration
#
# Requirements:
# - an XCP-ng host with blktap and tapback
# - a small unix VM with dd and blockdev in the guest, and internet access (to install randstream
#   and fio)

@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestVMBasicIO:
    def test_vm_read_write_10mb(self, host: Host, running_vm: VM, vhd_path: str,
                                vbd_connector: VBDConnector) -> None:
        """VM I/O on the device goes through tapdisk."""
        vm = running_vm
        device = "xvdb"
        pid, minor = vbd_connector.connect(vm, f"vhd:{vhd_path}", device)
        wait_for_guest_device(vm, device)

        write_then_read(vm, device, size_mib=10)
        assert_tapdisk_io(host, pid, minor, size_mib=10)

    def test_vm_device_appears_in_guest(self, running_vm: VM, vhd_path: str, vbd_connector: VBDConnector) -> None:
        """The device appears in the guest with the size of the image."""
        vm = running_vm
        device = "xvdc"
        vbd_connector.connect(vm, f"vhd:{vhd_path}", device)
        wait_for_guest_device(vm, device)

        size = int(vm.ssh(f'blockdev --getsize64 /dev/{device}'))
        assert size == 100 * MiB, \
            f"Device size is {size} bytes, expected 100 MiB (size of the VHD)"

    def test_vm_io_stress(self, host: Host, running_vm: VM, vhd_path: str, vbd_connector: VBDConnector) -> None:
        """Repeated VM I/O all go through tapdisk."""
        vm = running_vm
        device = "xvdd"
        pid, minor = vbd_connector.connect(vm, f"vhd:{vhd_path}", device)
        wait_for_guest_device(vm, device)

        iterations = 3
        for i in range(iterations):
            logging.info(f"I/O iteration {i + 1}/{iterations}")
            write_then_read(vm, device, size_mib=5)
        assert_tapdisk_io(host, pid, minor, size_mib=iterations * 5)

@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestVMConnectDisconnect:
    def test_connect_disconnect_cycle(self, host: Host, running_vm: VM, vhd_path: str,
                                      vbd_connector: VBDConnector) -> None:
        """The same image can be connected and disconnected several times in a row."""
        vm = running_vm
        device = "xvde"
        for cycle in range(2):
            logging.info(f"Connection cycle {cycle + 1}/2")
            pid, minor = vbd_connector.connect(vm, f"vhd:{vhd_path}", device)
            wait_for_guest_device(vm, device)
            vm.ssh(f'dd if=/dev/urandom of=/dev/{device} bs=1M count=1 conv=fsync status=none')

            vbd_connector.disconnect(vm, device)
            wait_for_guest_device(vm, device, present=False)
            assert_tapdisk_destroyed(host, pid, minor)

@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestTapbackIntegration:
    def test_connected_vbd_has_kthread(self, connected_vbd: ConnectedVBD, vbd_connector: VBDConnector,
                                       xenstore: XenStoreHelper) -> None:
        """Tapback publishes in kthread-pid the pid of the tapdisk serving the VBD."""
        backend = vbd_connector.backend_path(connected_vbd.vm, connected_vbd.device)
        kthread_pid = xenstore.read(f"{backend}/kthread-pid")
        assert kthread_pid == str(connected_vbd.pid), \
            f"kthread-pid is {kthread_pid}, expected {connected_vbd.pid} (pid of the tapdisk)"

@pytest.mark.small_vm
@pytest.mark.unix_vm
class TestPauseUnpause:
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

    @pytest.mark.parametrize('image_type', ['vhd', 'qcow2'])
    def test_pause_unpause_loop_during_io(self, host: Host, running_unix_vm_with_fio: VM, image_type: str,
                                          request: pytest.FixtureRequest, vbd_connector: VBDConnector,
                                          defer: Defer) -> None:
        """tapdisk survives repeated pause/unpause during guest I/O, and doesn't lose any request."""
        vm = running_unix_vm_with_fio
        image = f"{image_type}:{request.getfixturevalue(f'{image_type}_path')}"
        device = "xvdc"
        defer(lambda: vm.ssh('pkill -9 fio', check=False))
        pid, minor = vbd_connector.connect(vm, image, device)
        wait_for_guest_device(vm, device)
        # fio must outlive the pause/unpause cycles
        start_fio(vm, device, runtime=config.blktap_max_duration + 60)
        wait_for_guest_reads(host, pid, minor)

        tapctl = TapCtl(host)
        cycles = 0
        deadline = time.monotonic() + config.blktap_max_duration
        while time.monotonic() < deadline:
            cycles += 1
            tapctl.pause(pid, minor)
            # one cycle out of two, unpause with the image path, which makes tapdisk close and
            # reopen it, like SM does
            tapctl.unpause(pid, minor, image if cycles % 2 == 0 else None)
            # tapdisk answers, and serves the guest reads again: its stats move
            read_secs = tapctl.stats(pid, minor)['secs'][0]
            wait_for(lambda: tapctl.stats(pid, minor)['secs'][0] > read_secs,
                     f"Wait for guest reads after the unpause #{cycles}", timeout_secs=30)
        logging.info(f"{cycles} pause/unpause cycles done")

        # No request was lost across the pauses: the killed fio exits (a process waiting for a
        # lost request can't), and the device has no request in flight anymore
        vm.ssh('pkill -9 fio')
        wait_for(lambda: vm.ssh_with_result('pgrep fio').returncode != 0,
                 "Wait for fio to exit", timeout_secs=30)
        inflight = vm.ssh(f'cat /sys/block/{device}/inflight').split()
        assert inflight == ['0', '0'], \
            f"Requests still in flight on /dev/{device}: {inflight}"
        vbd_connector.disconnect(vm, device)
        wait_for_guest_device(vm, device, present=False)
        assert_tapdisk_destroyed(host, pid, minor)
