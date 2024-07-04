import logging
import os
import pytest

from lib import installer, pxe
from lib.common import wait_for, vm_image
from lib.pool import Pool

# FIXME without --ignore-unknown-dependency, SKIPPED
# "because it depends on tests/install/test.py::TestNested::test_firstboot_install[uefi-821.1-host1]"
@pytest.mark.usefixtures("xcpng_chained")
@pytest.mark.parametrize(("orig_version", "iso_version"), [
    ("821.1", "83rc1"),
])
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
                             param_mapping={"params": "orig_version", "firmware": "firmware"})
@pytest.mark.installer_iso(
    lambda version: {
        "83rc1": "xcpng-8.3-rc1",
    }[version],
    param_mapping={"version": "iso_version"})
def test_pool_rpu(firmware, orig_version, iso_version, iso_remaster, create_vms):
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

    # create pool with shared SR

    slave = Pool(slave_vm.ip).master
    slave.join_pool(pool)

    from data import NFS_DEVICE_CONFIG
    sr = pool.master.sr_create("nfs", "NFS Shared SR", NFS_DEVICE_CONFIG,
                               shared=True, verify=True)

    # create and start VMs
    vms = (
        pool.master.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid),
        pool.master.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid),
    )

    for vm in vms:
        vm.start()

    wait_for(lambda: all(vm.is_running() for vm in vms), "Wait for VMs running")
    wait_for(lambda: all(vm.try_get_and_store_ip() for vm in vms),
             "Wait for VM IP", timeout_secs=5*60)
    wait_for(lambda: all(vm.is_management_agent_up() for vm in vms),
             "Wait for management agent up")

    logging.info("VMs dispatched as %s", [vm.get_residence_host().uuid for vm in vms])

    # do RPU

    vms_to_migrate = [vm for vm in vms if vm.get_residence_host().uuid == pool.master.uuid]
    logging.info("Expecting migration of %s", (vms_to_migrate,))
    pool.master.xe("host-evacuate", {"host": pool.master.uuid})
    wait_for(lambda: all(vm.get_residence_host().uuid != pool.master.uuid for vm in vms),
             "Wait for management agent up")

    pool.master.shutdown()
    installer.perform_upgrade(iso=iso_remaster, host_vm=master_vm)
    master_vm.start()
    wait_for(master_vm.is_running, "Wait for Master VM running")
    wait_for(lambda: not os.system(f"nc -zw5 {master_vm.ip} 22"),
             "Wait for ssh back up on Master VM", retry_delay_secs=5)
    wait_for(pool.master.is_enabled, "Wait for XAPI to be ready", timeout_secs=30 * 60)

    vms_to_migrate = vms
    logging.info("Expecting migration of %s", (vms_to_migrate,))
    pool.master.xe("host-evacuate", {"host": slave.uuid})
    wait_for(lambda: all(vm.get_residence_host().uuid != slave.uuid for vm in vms),
             "Wait for management agent up")

    slave.shutdown()
    installer.perform_upgrade(iso=iso_remaster, host_vm=slave_vm)
    slave_vm.start()
    wait_for(slave_vm.is_running, "Wait for Slave VM running")
    wait_for(lambda: not os.system(f"nc -zw5 {slave_vm.ip} 22"),
             "Wait for ssh back up on Slave VM", retry_delay_secs=5)
    wait_for(slave.is_enabled, "Wait for XAPI to be ready", timeout_secs=30 * 60)

    logging.info("Migrating a VM back to slave")
    vms[1].migrate(slave)

    # cleanup
    slave.shutdown()
    pool.master.shutdown()
    wait_for(lambda: slave_vm.is_halted(), "Wait for Slave VM to be halted", timeout_secs=5*60)
    wait_for(lambda: master_vm.is_halted(), "Wait for Master VM to be halted", timeout_secs=5*60)
    # FIXME destroy shared SR contents
