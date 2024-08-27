import logging
import os
import pytest

from lib import pxe
from lib.common import wait_for
from lib.pool import Pool

from data import HOSTS_IP_CONFIG

MAINTESTS = "tests/install/test.py::TestNested"

# FIXME without --ignore-unknown-dependency, SKIPPED
# "because it depends on tests/install/test.py::TestNested::test_firstboot_install[uefi-821.1-host1-iso-nosr]"
@pytest.mark.usefixtures("xcpng_chained")
@pytest.mark.parametrize("mode", (
    "821.1",
))
@pytest.mark.parametrize("firmware", ("uefi", "bios"))
@pytest.mark.continuation_of(
    lambda mode, firmware: [
        dict(vm="vm1",
             image_test=f"{MAINTESTS}::test_firstboot_install[{firmware}-{mode}-host1-iso-nosr]",
             scope="session"),
        dict(vm="vm2",
             image_vm="vm1",
             image_test=f"{MAINTESTS}::test_firstboot_install[{firmware}-{mode}-host2-iso-nosr]",
             scope="session"),
    ])
def test_join_pool(firmware, mode, create_vms):
    (master_vm, slave_vm) = create_vms
    master_mac = master_vm.vifs()[0].param_get('MAC')
    logging.info("Master VM has MAC %s", master_mac)
    slave_mac = slave_vm.vifs()[0].param_get('MAC')
    logging.info("Slave VM has MAC %s", slave_mac)

    pxe.arp_clear_for(master_mac)
    master_vm.start()
    pxe.arp_clear_for(slave_mac)
    slave_vm.start()
    wait_for(master_vm.is_running, "Wait for master VM running")
    wait_for(slave_vm.is_running, "Wait for slave VM running")

    master_vm.ip = HOSTS_IP_CONFIG['HOSTS']['DEFAULT']
    slave_vm.ip = HOSTS_IP_CONFIG['HOSTS']['host2']

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
