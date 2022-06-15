import logging
import pytest
import time

from lib.commands import SSHCommandFailed
from lib.common import wait_for, vm_image

from tests.storage.linstor import create_linstor_sr

# Requirements:
# - one XCP-ng host >= 8.2 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

class TestLinstorSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_sr_without_linstor(self, hosts, vg_for_all_hosts):
        # This test must be the first in the series in this module
        master = hosts[0]
        assert not master.binary_exists('linstor'), \
            'linstor must not be installed on the host at the beginning of the tests'
        sr = None
        try:
            sr = create_linstor_sr('LINSTOR-SR-test', hosts)
        except Exception:
            logging.info('SR creation failed, as expected.')
        if sr is not None:
            sr.destroy()
            assert False, 'SR creation should not have succeeded!'

    def test_create_and_destroy_sr(self, hosts_with_linstor, vg_for_all_hosts):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        master = hosts_with_linstor[0]
        sr = create_linstor_sr('LINSTOR-SR-test', hosts_with_linstor)
        # import a VM in order to detect vm import issues here rather than in the vm_on_linstor_sr fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = master.import_vm(vm_image('mini-linux-x86_64-bios'), sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("linstor_sr")
class TestLinstorSR:
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
        vm.wait_for_os_booted()
        vm.test_snapshot_on_running_vm()
        vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_linstor_sr, host, linstor_sr):
        sr = linstor_sr
        vm = vm_on_linstor_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PDB attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.reboot
    def test_linstor_missing(self, linstor_sr, host):
        packages = ['python-linstor', 'linstor-client']
        sr = linstor_sr
        linstor_installed = True
        try:
            host.yum_remove(packages)
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
            host.yum_install(packages)
            linstor_installed = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not linstor_installed:
                host.yum_install(packages)

    # *** End of tests with reboots
