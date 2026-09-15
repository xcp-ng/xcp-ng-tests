import pytest

import logging
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor

from lib.common import exec_nofail, raise_errors
from lib.host import Host
from lib.vm import VM

from typing import Generator

# Requirements:
# - an XCP-ng host (--hosts) >= 8.2 with at least 2 CPUs
# - dom0 with at least 2 vCPUs
# - a VM (--vm)
# - enough space to import 4 VMs on default SR
# - the default SR must be either shared or local on master host, so that VMs can all start on the same host
# - guests need `nc`, with port 5001 reachable from the test runner

@pytest.fixture(scope='module')
def four_vms(imported_vm: VM) -> Generator[tuple[VM, VM, VM, VM], None, None]:
    vm1 = imported_vm
    vm2 = vm1.clone()
    vm3 = vm1.clone()
    vm4 = vm1.clone()
    yield (vm1, vm2, vm3, vm4)
    # teardown
    errors = []
    logging.info("< Destroy VM4")
    errors += exec_nofail(lambda: vm4.destroy())
    logging.info("< Destroy VM3")
    errors += exec_nofail(lambda: vm3.destroy())
    logging.info("< Destroy VM2")
    errors += exec_nofail(lambda: vm2.destroy())
    raise_errors(errors)

@pytest.fixture(scope='module')
def host_with_multi_vcpu_dom0(host: Host) -> Host:
    logging.info("Ensure that dom0 has at least 2 vCPUs")
    dom0_vcpus = int(host.ssh("nproc"))
    if dom0_vcpus < 2:
        pytest.fail(f"dom0 needs at least 2 vCPUs, found {dom0_vcpus}")
    return host

def connect(vm: VM, port: int) -> socket.socket:
    assert vm.ip is not None
    timeout = time.monotonic() + 10
    while True:
        try:
            return socket.create_connection((vm.ip, port), timeout=10)
        except ConnectionRefusedError:
            if time.monotonic() >= timeout:
                raise
            time.sleep(0.5)

@pytest.mark.flaky # sometimes IRQs are not balanced and we don't know why. And sometimes a VM doesn't report an IP.
@pytest.mark.small_vm
class TestIrqBalance:
    """
    In the past, a security fix broke IRQ balancing for VIFs.
    We want to avoid this to happen again, so this testcase runs several VMs
    and verifies that the IRQs are balanced on more than one CPU.
    """

    def test_start_four_vms(self, host_with_multi_vcpu_dom0: Host, four_vms: tuple[VM, VM, VM, VM]) -> None:
        for vm in four_vms:
            vm.start(on=host_with_multi_vcpu_dom0.uuid)

        port = 5001
        for vm in four_vms:
            vm.wait_for_vm_running_and_ssh_up()
            vm.ssh(f'nc -l -p {port} > /dev/null', background=True)

        logging.info("Create some network traffic for each VM")
        # Generate a traffic stream for about 2 irqbalance intervals
        # (10s is the interval in irqbalance-1.0.7-15.xcpng8.3.x86_64).
        stream_duration = 2 * 10
        stream_data = bytes(64 * 1024)

        def generate_stream(vm: VM) -> None:
            with connect(vm, port) as sock:
                deadline = time.monotonic() + stream_duration

                while time.monotonic() < deadline:
                    sock.sendall(stream_data)

        with ThreadPoolExecutor(max_workers=len(four_vms)) as executor:
            # consume results to re-raise exceptions
            list(executor.map(generate_stream, four_vms))

        logging.info("Check that the IRQs of the VMs VIFs are not all on the same CPU on dom0")
        cpus = set()
        for vm in four_vms:
            # List the CPU(s) that handled IRQs for the VM's vifs
            for vif in vm.vifs():
                device_id = vif.device_id()
                # depending on kernel patches, we're looking either for xen-dyn or xen-dyn-lateeoi
                output = vm.host.ssh(rf'grep /proc/interrupts -e "xen-dyn\(-lateeoi\)\?\\s\+-event\\s\+{device_id}-"')
                assert len(output) > 0
                for line in output.splitlines():
                    fields = line.split()
                    try:
                        xen_dyn_index = fields.index('xen-dyn')
                    except ValueError:
                        xen_dyn_index = fields.index('xen-dyn-lateeoi')
                    irqs_per_cpu = fields[1:xen_dyn_index]
                    for i, val in enumerate(irqs_per_cpu):
                        if int(val) > 0:
                            logging.info(f"VIF {device_id}: {val} IRQs for CPU {i}")
                            cpus.add(i)

        assert len(cpus) > 1, "there must be more than one CPU that handles the IRQs"
