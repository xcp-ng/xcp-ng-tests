import pytest

import logging

from lib.commands import SSHCommandFailed
from lib.common import vm_image
from lib.host import Host
from lib.pool import Pool

# Requirements:
# - two or more XCP-ng hosts >= 8.2 with additional unused disk(s) for the SR
# - access to XCP-ng RPM repository from the host

class TestLinstorSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_sr_without_linstor(
        self, host: Host, lvm_disks: None, provisioning_type: str, storage_pool_name: str
    ) -> None:
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

    def test_create_and_destroy_sr(
        self, pool_with_linstor: Pool, provisioning_type: str, storage_pool_name: str
    ) -> None:
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
