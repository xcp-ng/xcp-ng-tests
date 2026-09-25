from __future__ import annotations

import pytest

import logging
import string
import time

from lib import config
from lib.blktap import TapCtl, VBDConnector
from lib.common import wait_for
from lib.host import Host
from lib.vm import VM
from tests.storage.blktap import (
    assert_tapdisk_destroyed,
    start_fio,
    wait_for_guest_device,
    wait_for_guest_reads,
    wait_for_tapdisk_exit,
)

# Destroy tapdisks while the guest does I/O on their VBD (equivalent to test-hotunplug.sh from
# blktap-tools).
#
# The requests in flight when a tapdisk is destroyed must fail in the guest, so that the processes
# doing I/O on the device don't stay stuck. The guest keeps its device, though: hence a VM of its own
# (a clone of the imported one, with 4 vCPUs for multi-queue), destroyed afterwards, and a new device
# for each VBD.
#
# Requirements:
# - an XCP-ng host with blktap and tapback
# - a small unix VM, with internet access to install fio

@pytest.mark.skip(reason="Disabled for now")
@pytest.mark.small_vm
@pytest.mark.unix_vm
# several vCPUs, so that blkfront uses several queues with a qcow2 image: blkfront uses at most one per vCPU
@pytest.mark.parametrize('vm_with_vcpu_count', [4], indirect=True)
@pytest.mark.parametrize('image', ['vhd', 'qcow2'], indirect=True)
def test_destroy_tapdisk_during_io(host: Host, vm_with_fio: VM, image: str, vbd_connector: VBDConnector) -> None:
    """Destroying tapdisks while the guest does I/O on them releases them, and leaves the host usable."""
    vm = vm_with_fio
    tapctl = TapCtl(host)
    # one device per VBD: the devices of the destroyed tapdisks stay stuck in the guest
    devices = [f"xvd{letter}" for letter in string.ascii_lowercase[1:]]
    iterations = config.blktap.destroy_iterations
    assert iterations <= len(devices), \
        f"At most {len(devices)} iterations, one device name each"

    for iteration, device in enumerate(devices[:iterations]):
        logging.info(f"Destroy during I/O {iteration + 1}/{iterations}, on {device}")
        pid, minor = vbd_connector.connect(vm, image, device)
        wait_for_guest_device(vm, device)
        fio_start = time.monotonic()
        # with barriers: destroying a VBD with barriers pending on several queues could wait forever
        start_fio(vm, device, runtime=20, barriers=True)
        wait_for_guest_reads(host, pid, minor)
        # destroy the tapdisk 10 s after the start of fio, while the guest I/O go on
        time.sleep(max(0, 10 - (time.monotonic() - fio_start)))

        # A tapdisk whose destroy doesn't return within 1 min is stuck: kill it, then fail once
        # the VBD is cleaned up
        result = host.ssh_with_result(f'timeout 60 tap-ctl destroy -p {pid} -m {minor}')
        destroy_timed_out = result.returncode == 124  # exit status of timeout
        if destroy_timed_out:
            logging.warning(f"tap-ctl destroy of tapdisk {pid} timed out: kill it")
            host.ssh(f'kill -9 {pid}', check=False)
        elif result.returncode != 0:
            # with requests in flight, the destroy can report errors: what matters is that the
            # tapdisk goes away and releases its minor
            logging.warning(f"Destroy with I/O in flight: {result.stdout.strip()}")
        wait_for_tapdisk_exit(host, pid)
        # tap-ctl list shows an allocated minor without tapdisk as "minor=N"
        if host.ssh_with_result(f'tap-ctl list -m {minor}').stdout.strip():
            tapctl.free(minor)
        assert_tapdisk_destroyed(host, pid, minor)
        # the tapdisk is gone and can't be probed: only the kernel can tell whether it crashed
        crashes = host.ssh_with_result(f"dmesg | grep -F 'tapdisk[{pid}]:'").stdout.strip()
        assert not crashes, \
            f"tapdisk {pid} crashed:\n{crashes}"

        # Remove the VBD from XenStore: the guest doesn't close its device, stuck by the lost
        # requests, so detach() waits for the Closed state in vain before removing the entries
        vbd_connector.detach(vm, device)
        vm.ssh('true')  # the guest still answers
        assert not destroy_timed_out, \
            f"tap-ctl destroy of tapdisk {pid} didn't return within 1 min"

        # The I/O on the removed device must fail, and fio exit. Kill it anyway, before starting a new
        # one: a process waiting for a request that the destroyed tapdisk never answered doesn't die
        # (state D), and still holds its memory, which can prevent the next fio from starting.
        vm.ssh('pkill -9 fio', check=False)
        wait_for(lambda: vm.ssh_with_result('pgrep fio').returncode != 0,
                 f"Wait for the fio processes on {device} to exit", timeout_secs=30)
