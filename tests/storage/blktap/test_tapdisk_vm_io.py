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
    set_guest_scheduler,
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

# fio block sizes of the pause/unpause loop
BLOCK_SIZES = {
    'small': ('4k', '8k', '16k', '64k'),
    'large': ('256k', '1M', '4M'),
    'mixed': ('4k', '16k', '256k', '1M'),
}
# short pauses, a longer one now and then; the guest keeps working between two pauses
SHORT_HOLD_SECS = (0.05, 1.0)
LONG_HOLD_SECS = 30
LONG_HOLD_EVERY = 50
RUN_SECS = (0.2, 2.0)
VALIDATE_EVERY = 20
RESUME_TIMEOUT_SECS = 60

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
class TestPauseUnpause:
    @pytest.mark.parametrize('image', ['vhd', 'qcow2'], indirect=True)
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

    # several vCPUs, so that blkfront uses several queues with a qcow2 image
    @pytest.mark.parametrize('vm_with_vcpu_count', [4], indirect=True)
    @pytest.mark.parametrize('bench_image', ['qcow2', 'vhd'], indirect=True)
    @pytest.mark.parametrize('block_profile', list(BLOCK_SIZES))
    def test_pause_unpause_loop_during_io(self, host: Host, vm_with_fio: VM, bench_image: str, block_profile: str,
                                          vbd_connector: VBDConnector, defer: Defer) -> None:
        """tapdisk survives many pause/unpause during guest I/O with fsyncs: no hang, no request or data lost."""
        vm = vm_with_fio
        device = "xvdb"
        dev = f"/dev/{device}"
        defer(lambda: vm.ssh('pkill -9 fio', check=False))
        pid, minor = vbd_connector.connect(vm, bench_image, device)
        wait_for_guest_device(vm, device)
        set_guest_scheduler(vm, device, config.blktap.bench_scheduler)

        # Random data in the first half of the device, which fio leaves alone: it must be read back
        # unchanged
        seed = random.randrange(2**32)
        logging.info(f"Random seed: {seed}")
        install_randstream(vm)
        dev_size = int(vm.ssh(f'blockdev --getsize64 {dev}'))
        layout = compute_span_layout(dev_size // 2, 24 * MiB, 3, config.write_volume_align)
        spans = [StreamSpan(position=position, size=size) for position, size in layout]
        for i, span in enumerate(spans):
            span.generate(vm, dev, seed=seed + i)

        def validate_spans() -> None:
            # not read back from the guest page cache
            vm.ssh(f'blockdev --flushbufs {dev}')
            for span in spans:
                span.validate(vm, dev)

        # random I/O with many requests in flight on the second half, and synchronous writes followed by
        # a fsync (barriers); fio must outlive the pause/unpause cycles
        start_fio(vm, device, runtime=config.blktap.max_duration + 60, access_types=('randrw', 'randwrite'),
                  block_sizes=BLOCK_SIZES[block_profile], barriers=True, offset='50%')
        wait_for_guest_reads(host, pid, minor)

        tapctl = TapCtl(host)
        rng = random.Random(seed)
        cycle = 0
        deadline = time.monotonic() + config.blktap.max_duration
        while time.monotonic() < deadline:
            cycle += 1
            hold = LONG_HOLD_SECS if cycle % LONG_HOLD_EVERY == 0 else rng.uniform(*SHORT_HOLD_SECS)
            before = tapctl.stats(pid, minor)['secs']
            tapctl.pause(pid, minor)
            time.sleep(hold)
            # one cycle out of two, unpause with the image path, which makes tapdisk close and
            # reopen it, like SM does
            tapctl.unpause(pid, minor, bench_image if cycle % 2 == 0 else None)
            logging.info(f"Cycle {cycle}: held {hold:.2f} s, unpaused")
            # the guest requests that waited during the pause are served, and the new ones too
            wait_for(lambda: all(now > then for now, then in zip(tapctl.stats(pid, minor)['secs'], before)),
                     f"Wait for the guest reads and writes after the unpause #{cycle}",
                     timeout_secs=RESUME_TIMEOUT_SECS, retry_delay_secs=1)
            if cycle % VALIDATE_EVERY == 0:
                validate_spans()
            time.sleep(rng.uniform(*RUN_SECS))
        validate_spans()
        logging.info(f"{cycle} pause/unpause cycles done")

        # No request was lost across the pauses: fio is still running, the killed fio exits (a process
        # waiting for a lost request can't), and the device has no request in flight anymore
        assert vm.ssh_with_result('pgrep fio').returncode == 0, \
            "fio exited before the end of the pause/unpause cycles"
        vm.ssh('pkill -9 fio')
        wait_for(lambda: vm.ssh_with_result('pgrep fio').returncode != 0,
                 "Wait for fio to exit", timeout_secs=30)
        inflight = vm.ssh(f'cat /sys/block/{device}/inflight').split()
        assert inflight == ['0', '0'], \
            f"Requests still in flight on /dev/{device}: {inflight}"
        vbd_connector.disconnect(vm, device)
        wait_for_guest_device(vm, device, present=False)
        assert_tapdisk_destroyed(host, pid, minor)
