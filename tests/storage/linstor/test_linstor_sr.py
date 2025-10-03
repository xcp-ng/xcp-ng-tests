import pytest

import logging
import time

from lib.commands import SSHCommandFailed
from lib.common import vm_image, wait_for
from tests.storage import vdi_is_open

from .conftest import LINSTOR_PACKAGE

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
    @pytest.mark.upgrade_test
    def test_linstor_sr_pool_update(self, linstor_sr, vm_on_linstor_sr):
        """
        Perform update on the Linstor SR pool hosts while ensuring VM availability.
        1. Identify all hosts in the SR pool and order them with the master first.
        2. Update all hosts if updates are available.
        3. Reboot all hosts.
        4. Sequentially ensure that the VM can start on all hosts.
        """
        import concurrent.futures, threading

        sr = linstor_sr
        vm = vm_on_linstor_sr
        updates_applied = []
        updates_lock = threading.Lock()

        # Sort hosts so that pool master is first (optional)
        hosts = sorted(sr.pool.hosts, key=lambda h: h != sr.pool.master)

        # RPU is disabled for pools with XOSTOR SRs.
        # LINSTOR expects that we always use satellites and controllers with the same version on all hosts.
        def install_updates_on(host):
            logging.info("Checking on host %s", host.hostname_or_ip)
            if host.has_updates(enablerepo="xcp-ng-linstor-testing"):
                host.install_updates(enablerepo="xcp-ng-linstor-testing")
                with updates_lock:
                    updates_applied.append(host)
            else:
                logging.info("No updates available for host %s", host.hostname_or_ip)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            executor.map(install_updates_on, hosts)

        # Reboot updated hosts
        def reboot_updated(host):
            host.reboot(verify=True)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            executor.map(reboot_updated, updates_applied)

        # Ensure VM is able to boot on all the hosts
        for h in hosts:
            vm.start(on=h.uuid)
            vm.wait_for_os_booted()
            vm.shutdown(verify=True)

        sr.scan()

    # *** End of tests with reboots

# --- Test diskless resources --------------------------------------------------

def _get_diskful_hosts(host, controller_option, sr_group_name, vdi_uuid):
    # TODO: If any resource is in a temporary creation state or unknown, then need to wait intelligently.
    attempt = 0
    retries = 3
    sleep_sec = 5

    while attempt < retries:
        try:
            # Get volume name from VDI UUID
            # "xcp/volume/{vdi_uuid}/volume-name": "{volume_name}"
            volume_name = host.ssh([
                "linstor-kv-tool", "--dump-volumes", "-g", sr_group_name,
                "|", "grep", "volume-name", "|", "grep", f"/{vdi_uuid}/"
            ]).split(': ')[1].split('"')[1]

            # Find host where volume is UpToDate
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
        except SSHCommandFailed as e:
            logging.error("SSH Command Failed (attempt %d/%d): %s", attempt + 1, retries, e)
            attempt += 1
            if attempt >= retries:
                raise
            time.sleep(sleep_sec)

def _ensure_resource_remain_diskless(host, controller_option, sr_group_name, vdi_uuid, diskless):
    diskfuls = _get_diskful_hosts(host, controller_option, sr_group_name, vdi_uuid)
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
        diskfuls = _get_diskful_hosts(host, controller_option, sr_group_name, vdi_uuid)
        diskless = []
        for member in host.pool.hosts:
            if member.param_get("name-label").lower() not in diskfuls:
                diskless += [member]
        assert diskless

        # Start VM on host with diskless resource
        vm.start(on=diskless[0].uuid)
        vm.wait_for_os_booted()
        _ensure_resource_remain_diskless(host, controller_option, sr_group_name, vdi_uuid, diskless)

        vm.shutdown(verify=True)
        _ensure_resource_remain_diskless(host, controller_option, sr_group_name, vdi_uuid, diskless)
