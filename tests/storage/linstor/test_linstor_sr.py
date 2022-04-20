import logging
import pytest
import time

from .conftest import GROUP_NAME, create_linstor_sr, destroy_linstor_sr
from lib.commands import SSHCommandFailed
from lib.common import wait_for, vm_image

# Requirements:
# - one XCP-ng host >= 8.2 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host
# - a repo with the LINSTOR RPMs must be given using the command line param `--additional-repos`

class TestLinstorSRCreateDestroy:
    vm = None

    def test_create_sr_without_linstor(self, hosts, lvm_disks):
        master = hosts[0]

        # This test must be the first in the series in this module
        assert not master.binary_exists('linstor'), \
            "linstor must not be installed on the host at the beginning of the tests"
        try:
            sr = master.sr_create('linstor', 'LINSTOR-SR-test', {
                'hosts': ','.join([host.hostname() for host in hosts]),
                'group-name': GROUP_NAME,
                'redundancy': len(hosts),
                'provisioning': 'thick'
            }, shared=True)
            try:
                sr.destroy()
            except Exception:
                pass
            assert False, "SR creation should not have succeeded!"
        except SSHCommandFailed as e:
            logging.info("SR creation failed, as expected: {}".format(e))

    def test_create_and_destroy_sr(self, hosts_with_linstor, lvm_disks):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        master = hosts_with_linstor[0]
        sr = create_linstor_sr(hosts_with_linstor)
        # import a VM in order to detect vm import issues here rather than in the vm_on_linstor_sr fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = master.import_vm(vm_image('mini-linux-x86_64-bios'), sr.uuid)
        vm.destroy(verify=True)
        destroy_linstor_sr(hosts_with_linstor, sr)

@pytest.mark.usefixtures("linstor_sr")
class TestLinstorSR:
    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_linstor_sr):
        vm = vm_on_linstor_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_snapshot(self, vm_on_linstor_sr):
        vm = vm_on_linstor_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.test_snapshot_on_running_vm()
        vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot # reboots the host
    @pytest.mark.small_vm # run with a small VM to test the features
    def test_reboot(self, vm_on_linstor_sr, host, linstor_sr):
        sr = linstor_sr
        vm = vm_on_linstor_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PDB attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.reboot # reboots the host
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
