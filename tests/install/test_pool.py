import logging
import os
import pytest

from lib import pxe
from lib.common import wait_for
from lib.pool import Pool

@pytest.mark.usefixtures("xcpng_chained")
@pytest.mark.parametrize("mode", (
    "821.1",
))
@pytest.mark.parametrize("firmware", ("uefi", "bios"))
@pytest.mark.continuation_of(lambda params, firmware: [
    dict(vm="vm1",
         image_test=f"tests/install/test.py::TestNested::test_firstboot_install[{firmware}-{params}-host1]",
         scope="package"),
    dict(vm="vm2",
         image_vm="vm1",
         image_test=f"tests/install/test.py::TestNested::test_firstboot_install[{firmware}-{params}-host2]",
         scope="package"),
],
                             param_mapping={"params": "mode", "firmware": "firmware"})
def test_join_pool(firmware, mode, create_vms):
    (master_vm, slave_vm) = create_vms
    master_mac = master_vm.vifs()[0].param_get('MAC')
    slave_mac = slave_vm.vifs()[0].param_get('MAC')

    master_vm.start()
    slave_vm.start()
    wait_for(master_vm.is_running, "Wait for master VM running")
    wait_for(slave_vm.is_running, "Wait for slave VM running")

    # catch host-vm IP address
    wait_for(lambda: pxe.arp_addresses_for(master_mac),
             "Wait for DHCP server to see Master VM in ARP tables",
             timeout_secs=10*60)
    ips = pxe.arp_addresses_for(master_mac)
    logging.info("Master VM has IPs %s", ips)
    assert len(ips) == 1
    master_vm.ip = ips[0]

    wait_for(lambda: pxe.arp_addresses_for(slave_mac),
             "Wait for DHCP server to see Slave VM in ARP tables",
             timeout_secs=10*60)
    ips = pxe.arp_addresses_for(slave_mac)
    logging.info("Slave VM has IPs %s", ips)
    assert len(ips) == 1
    slave_vm.ip = ips[0]

    wait_for(lambda: not os.system(f"nc -zw5 {master_vm.ip} 22"),
             "Wait for ssh up on Master VM", retry_delay_secs=5)
    wait_for(lambda: not os.system(f"nc -zw5 {slave_vm.ip} 22"),
             "Wait for ssh up on Slave VM", retry_delay_secs=5)

    pool = Pool(master_vm.ip)
    slave = Pool(slave_vm.ip).master
    slave.join_pool(pool)

    slave.shutdown()
    pool.master.shutdown()

    wait_for(lambda: slave_vm.is_halted(), "Wait for Slave VM to be halted")
    wait_for(lambda: master_vm.is_halted(), "Wait for Master VM to be halted")
