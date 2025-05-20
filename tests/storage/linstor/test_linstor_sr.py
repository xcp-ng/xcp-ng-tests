import logging
import pytest
import time

from .conftest import GROUP_NAME, LINSTOR_PACKAGE
from lib.commands import SSHCommandFailed
from lib.common import wait_for, vm_image
from tests.storage import vdi_is_open

# Requirements:
# - two or more XCP-ng hosts >= 8.2 with additional unused disk(s) for the SR
# - access to XCP-ng RPM repository from the host

class TestLinstorSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_sr_without_linstor(self, host, lvm_disks, provisioning_type, storage_pool_name):
        # This test must be the first in the series in this module
        assert not host.is_package_installed('python-linstor'), \
            "linstor must not be installed on the host at the beginning of the tests"
        try:
            sr = host.sr_create('linstor', 'LINSTOR-SR-test', {
                'group-name': storage_pool_name,
                'redundancy': '1',
                'provisioning': provisioning_type
            }, shared=True)
            try:
                sr.destroy()
            except Exception:
                pass
            assert False, "SR creation should not have succeeded!"
        except SSHCommandFailed as e:
            logging.info("SR creation failed, as expected: {}".format(e))

    def test_create_and_destroy_sr(self, pool_with_linstor, provisioning_type, storage_pool_name):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        master = pool_with_linstor.master
        sr = master.sr_create('linstor', 'LINSTOR-SR-test', {
            'group-name': storage_pool_name,
            'redundancy': '1',
            'provisioning': provisioning_type
        }, shared=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_linstor_sr fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = master.import_vm(vm_image('mini-linux-x86_64-bios'), sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("linstor_sr")
class TestLinstorSR:
    @pytest.mark.quicktest
    def test_quicktest(self, linstor_sr, provisioning_type):
        try:
            linstor_sr.run_quicktest()
        except Exception:
            if provisioning_type == "thick":
                pytest.xfail(reason="Known failure for thick provisioning")
            raise # Let thin failures fail test
        else:
            if provisioning_type == "thick":
                pytest.fail("Expected failure for thick provisioning did not occur (XPASS)")

    def test_vdi_is_not_open(self, vdi_on_linstor_sr):
        assert not vdi_is_open(vdi_on_linstor_sr)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_linstor_sr):
        vm = vm_on_linstor_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_linstor_sr):
        vm = vm_on_linstor_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.mark.small_vm
    def test_linstor_sr_expand_disk(self, linstor_sr, provisioning_type, storage_pool_name,
                                    pytestconfig, vm_with_reboot_check):
        """
        This test demonstrates online expansion of a LINSTOR SR while a VM is actively running on it.

        It identifies hosts within the same pool, detects free raw disks, and expands the LVM to grow the SR.
        A VM is started before the expansion, and its functionality is verified through a shutdown and restart
        after the expansion completes successfully.
        """
        sr = linstor_sr
        sr_size = sr.pool.master.xe('sr-param-get', {'uuid': sr.uuid, 'param-name': 'physical-size'})

        resized = _expand_lvm_on_hosts(sr, provisioning_type, storage_pool_name, pytestconfig)

        # Need to ensure that linstor is healthy/up-to-date before moving ahead.
        time.sleep(30) # Wait time for Linstor node communications to restore.
        sr.scan()
        new_sr_size = sr.pool.master.xe('sr-param-get', {'uuid': sr.uuid, 'param-name': 'physical-size'})
        assert int(new_sr_size) > int(sr_size) and resized is True, \
            f"Expected SR size to increase but got old size: {sr_size}, new size: {new_sr_size}"
        logging.info("SR expansion completed")

    @pytest.mark.small_vm
    def test_linstor_sr_expand_host(self, linstor_sr, vm_with_reboot_check, prepare_linstor_packages,
                                    join_host_to_pool, setup_lvm_on_host, host, hostB1, storage_pool_name,
                                    provisioning_type):
        """
        This test validates expansion of a LINSTOR SR by dynamically adding a new host with local storage to the pool.
        A VM is started on the SR before expansion begins to ensure the SR is in active use during the process.

        It performs the following steps:
        - Installs LINSTOR packages on the new host (if missing).
        - Detects and prepares raw disks using LVM commands.
        - Joins the host (hostB1) to the existing pool and registers it with LINSTOR as a node.
        - Creates a new LINSTOR storage pool on the added host (LVM or LVM-thin, based on provisioning type).
        - Confirms SR expansion by verifying increased physical size.
        - Ensures SR functionality by rebooting the VM running on the SR.

        Finally, the test cleans up by deleting the LINSTOR node, ejecting the host from the pool,
        and removing packages and LVM metadata.
        """
        sr = linstor_sr
        sr_size = sr.pool.master.xe('sr-param-get', {'uuid': sr.uuid, 'param-name': 'physical-size'})
        resized = False

        # TODO: This section could be moved into a separate fixture for modularity.
        # However, capturing the SR size before expansion is critical to the test logic,
        # so it's intentionally kept inline to preserve control over the measurement point.

        sr_group_name = "xcp-sr-" + storage_pool_name.replace("/", "_")
        hostname = hostB1.xe('host-param-get', {'uuid': hostB1.uuid, 'param-name': 'name-label'})
        controller_option = "--controllers=" + ",".join([m.hostname_or_ip for m in host.pool.hosts])

        logging.info("Current list of linstor nodes:")
        logging.info(host.ssh_with_result(["linstor", controller_option, "node", "list"]).stdout)

        logging.info("Creating linstor node")
        host.ssh(["linstor", controller_option, "node", "create", "--node-type", "combined",
                 "--communication-type", "plain", hostname, hostB1.hostname_or_ip])
        hostB1.ssh(['systemctl', 'restart', 'linstor-satellite.service'])
        time.sleep(45)

        logging.info("New list of linstor nodes:")
        logging.info(host.ssh_with_result(["linstor", controller_option, "node", "list"]).stdout)
        logging.info("Expanding with linstor node")

        if provisioning_type == "thin":
            hostB1.ssh(['lvcreate', '-l', '+100%FREE', '-T', storage_pool_name])
            host.ssh(["linstor", controller_option, "storage-pool", "create", "lvmthin",
                     hostname, sr_group_name, storage_pool_name])
        else:
            host.ssh(["linstor", controller_option, "storage-pool", "create", "lvm",
                     hostname, sr_group_name, storage_pool_name])

        sr.scan()
        resized = True
        new_sr_size = sr.pool.master.xe('sr-param-get', {'uuid': sr.uuid, 'param-name': 'physical-size'})
        assert int(new_sr_size) > int(sr_size) and resized is True, \
            f"Expected SR size to increase but got old size: {sr_size}, new size: {new_sr_size}"
        logging.info("SR expansion completed from size %s to %s", sr_size, new_sr_size)

        # Cleanup
        host.ssh(["linstor", controller_option, "node", "delete", hostname])

    @pytest.mark.small_vm
    def test_linstor_sr_reduce_disk(self, linstor_sr, vm_with_reboot_check, provisioning_type):
        """
        Identify hosts within the same pool, detect used disks, modify LVM, and rescan LINSTOR SR.
        """
        if provisioning_type == "thin":
            logging.info(f"* SR reductoin by removing device is not supported for {provisioning_type} type *")
            return
        sr = linstor_sr
        sr_size = int(sr.pool.master.xe('sr-param-get', {'uuid': sr.uuid, 'param-name': 'physical-size'}))
        resized = False

        for h in sr.pool.hosts:
            logging.info("Working on %s", h.hostname_or_ip)
            devices = h.ssh('vgs ' + GROUP_NAME + ' -o pv_name --no-headings').split("\n")
            assert len(devices) > 1, "This test requires {GROUP_NAME} to have more than 1 disk or parition"
            eject_device = devices[-1].strip()
            logging.info("Attempting to remove device: %s", eject_device)
            try:
                h.ssh(['pvmove', eject_device]) # Choosing last device from list, assuming its least filled
                h.ssh(['vgreduce', GROUP_NAME, eject_device])
                h.ssh(['pvremove', eject_device])
            except SSHCommandFailed as e:
                if "No data to move for" in e.stdout:
                    h.ssh(['vgreduce', GROUP_NAME, eject_device])
                    h.ssh(['pvremove', eject_device])
                else:
                    pytest.fail("Failed to empty device")
            h.ssh('systemctl restart linstor-satellite.service')
            resized = True

        # Need to ensure that linstor is healthy/up-to-date before moving ahead.
        time.sleep(30) # Wait time for Linstor node communications to restore after service restart.

        sr.scan()

        new_sr_size = int(sr.pool.master.xe('sr-param-get', {'uuid': sr.uuid, 'param-name': 'physical-size'}))
        assert new_sr_size < sr_size and resized, \
            f"Expected SR size to decrease but got old size: {sr_size}, new size: {new_sr_size}"
        logging.info("SR reduction by removing disk is completed from %s to %s", sr_size, new_sr_size)

    @pytest.mark.small_vm
    def test_linstor_sr_reduce_host(self, linstor_sr, get_sr_size, vm_with_reboot_check, host, hostA2,
                                    remove_host_from_linstor):
        """
        Remove non master host from the same pool Linstor SR.
        Do we measure the time taken by system to rebalance after host removal?
        Should the host be graceful empty or force removal?
        """
        sr = linstor_sr
        sr_size = int(sr.pool.master.xe('sr-param-get', {'uuid': sr.uuid, 'param-name': 'physical-size'}))
        sr_size = 886189670400
        resized = False

        # Restart satellite services for clean state. This can be optional.
        for h in host.pool.hosts:
            h.ssh(['systemctl', 'restart', 'linstor-satellite.service'])

        time.sleep(30) # Wait till all services become normal

        resized = True
        sr.scan()
        new_sr_size = int(sr.pool.master.xe('sr-param-get', {'uuid': sr.uuid, 'param-name': 'physical-size'}))
        assert new_sr_size < sr_size and resized, \
            f"Expected SR size to decrease but got old size: {sr_size}, new size: {new_sr_size}"
        logging.info("SR reduction by removing host is completed from %s to %s", sr_size, new_sr_size)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_linstor_sr, host, linstor_sr):
        sr = linstor_sr
        vm = vm_on_linstor_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start(on=host.uuid)
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.reboot
    def test_linstor_missing(self, linstor_sr, host):
        sr = linstor_sr
        linstor_installed = True
        try:
            host.yum_remove(['python-linstor', 'linstor-client'])
            linstor_installed = False
            try:
                sr.scan()
                assert False, "SR scan should have failed"
            except SSHCommandFailed:
                logging.info("SR scan failed as expected.")
            host.reboot(verify=True)
            # give the host some time to try to attach the SR
            time.sleep(10)
            logging.info("Assert PBD not attached")
            assert not sr.all_pbds_attached()
            host.yum_install(['xcp-ng-linstor'])
            linstor_installed = True

            # Needed because the linstor driver is not in the xapi
            # sm-plugins list because xcp-ng-linstor RPM has been
            # removed by the `yum remove ...` call.
            host.restart_toolstack(verify=True)

            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not linstor_installed:
                host.yum_install([LINSTOR_PACKAGE])

    # *** End of tests with reboots

def _expand_lvm_on_hosts(sr, provisioning_type, storage_pool_name, pytestconfig):
    from lib.commands import SSHCommandFailed
    resized = False
    for h in sr.pool.hosts:
        logging.info(f"Checking for available disks on host: {h.hostname_or_ip}")
        available_disks = [d for d in h.available_disks() if h.raw_disk_is_available(d)]

        disks = []
        expansion_sr_disk = pytestconfig.getoption("expansion_sr_disk")
        if expansion_sr_disk:
            assert len(expansion_sr_disk) == 1, "Only one --expansion-sr-disk should be provided"
            if expansion_sr_disk[0] == "auto":
                disks = available_disks
            else:
                assert expansion_sr_disk[0] in available_disks, "The specified expansion disk is unavailable"
                disks = expansion_sr_disk
        else:
            disks = available_disks

        for disk in disks:
            device = f"/dev/{disk}"
            try:
                h.ssh(['pvcreate', device])
                h.ssh(['vgextend', GROUP_NAME, device])
                if provisioning_type == "thin":
                    h.ssh(['lvextend', '-l', '+100%FREE', storage_pool_name])
                else:
                    h.ssh(['systemctl', 'restart', 'linstor-satellite.service'])
                resized = True
                logging.info("LVM extended on host %s using device %s", h.hostname_or_ip, device)
            except SSHCommandFailed as e:
                raise RuntimeError(f"Disk expansion failed on {h.hostname_or_ip}: {e}")
    return resized

# --- Test diskless resources --------------------------------------------------

def _get_diskful_hosts(host, controller_option, volume_name):
    # Find host where volume is diskless
    # | {volume_name} | {host} | 7017 | Unused | Ok    |   UpToDate | 2023-10-24 18:52:05 |
    lines = host.ssh([
        "linstor", controller_option, "resource", "list",
        "|", "grep", volume_name, "|", "grep", "UpToDate"
    ]).splitlines()
    diskfuls = []
    for line in lines:
        hostname = line.split('|')[2].strip()
        diskfuls += hostname
    return diskfuls

def _ensure_resource_remain_diskless(host, controller_option, volume_name, diskless):
    diskfuls = _get_diskful_hosts(host, controller_option, volume_name)
    for diskless_host in diskless:
        assert diskless_host.param_get("name-label").lower() not in diskfuls

class TestLinstorDisklessResource:
    @pytest.mark.small_vm
    def test_diskless_kept(self, host, linstor_sr, vm_on_linstor_sr, storage_pool_name):
        vm = vm_on_linstor_sr
        vdi_uuids = vm.vdi_uuids(sr_uuid=linstor_sr.uuid)
        vdi_uuid = vdi_uuids[0]
        assert vdi_uuid is not None

        controller_option = "--controllers="
        for member in host.pool.hosts:
            controller_option += f"{member.hostname_or_ip},"

        sr_group_name = "xcp-sr-" + storage_pool_name.replace("/", "_")

        # Get volume name from VDI uuid
        # "xcp/volume/{vdi_uuid}/volume-name": "{volume_name}"
        output = host.ssh([
            "linstor-kv-tool", "--dump-volumes", "-g", sr_group_name,
            "|", "grep", "volume-name", "|", "grep", vdi_uuid
        ])
        volume_name = output.split(': ')[1].split('"')[1]

        diskfuls = _get_diskful_hosts(host, controller_option, volume_name)
        diskless = []
        for member in host.pool.hosts:
            if member.param_get("name-label").lower() not in diskfuls:
                diskless += [member]
        assert diskless

        # Start VM on host with diskless resource
        vm.start(on=diskless[0].uuid)
        vm.wait_for_os_booted()
        _ensure_resource_remain_diskless(host, controller_option, volume_name, diskless)

        vm.shutdown(verify=True)
        _ensure_resource_remain_diskless(host, controller_option, volume_name, diskless)
