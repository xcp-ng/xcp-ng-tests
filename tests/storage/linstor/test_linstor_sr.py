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

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_linstor_sr_fail_disk(self, linstor_sr, vm_on_linstor_sr, provisioning_type):
        """
        Identify random host within the same pool, detect used disks, fail one, and test VM useability on LINSTOR SR.
        """
        import random

        sr = linstor_sr
        if provisioning_type == "thick":
            time.sleep(45) # Let xcp-persistent-database come in sync across the nodes

        vm = vm_on_linstor_sr

        # Fail a disk from random host of Linstor pool
        try:
            random_host = random.choice(sr.pool.hosts) # TBD: Choose Linstor Diskfull node
            logging.info("Working on %s", random_host.hostname_or_ip)
            devices = random_host.ssh('vgs ' + GROUP_NAME + ' -o pv_name --no-headings').split("\n")
            # Choosing last device from list, assuming its least filled
            fail_device = devices[-1].strip() # /dev/sdb
            fail_device = random_host.ssh(['lsblk', fail_device, '--nodeps --output NAME --noheadings']) # sdb
            logging.info("Attempting to fail device: %s", fail_device)
            random_host.ssh(['echo', '"offline"', '>', '/sys/block/' + fail_device + '/device/state'])
        except Exception as e:
            # Offline disk shall connect back after host reboot. Teardown normally.
            random_host.reboot(verify=True)
            pytest.fail("Failed to simulate device failure. Error %s", e.stdout)

        # Ensure that VM is able to start on all hosts despite Linstor pool disk failure
        for h in sr.pool.hosts:
            logging.info(f"Checking VM on host {h.hostname_or_ip}")
            vm.start(on=h.uuid)
            vm.wait_for_os_booted()
            vm.shutdown(verify=True)

        random_host.reboot(verify=True)

        # Ensure PBDs are attached post reboot
        if not sr.all_pbds_attached():
            sr.plug_pbds()

        # Ensure SR scan works and proceed for teardown
        sr.scan()

    # *** End of tests with reboots

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
