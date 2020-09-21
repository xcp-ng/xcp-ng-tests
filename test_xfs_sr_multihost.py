import pytest
from lib.common import cold_migration_then_come_back, live_storage_migration_then_come_back

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.2 with an additional unused disk for the SR.
# - hostA2: Second member of the pool. Can have any local SR.
# - hostB1: Master of a second pool. Any local SR.
# From --vm parameter
# - A VM to import to the XFS SR
# And:
# - access to XCP-ng RPM repository from hostA1

@pytest.fixture(scope='module')
def xfs_sr(host, sr_disk):
    """ a XFS SR on first host """
    assert not host.file_exists('/usr/sbin/mkfs.xfs'), \
        "xfsprogs must not be installed on the host at the beginning of the tests"
    host.yum_install(['xfsprogs'], save_state=True)
    sr = host.sr_create('xfs', "XFS-local-SR", {'device': '/dev/' + sr_disk})
    yield sr
    # teardown
    sr.destroy()
    host.yum_restore_saved_state()

@pytest.fixture(scope='module')
def vm_on_xfs_sr(host, xfs_sr, vm_ref):
    print(">> ", end='')
    vm = host.import_vm_url(vm_ref, sr_uuid=xfs_sr.uuid)
    yield vm
    # teardown
    print("<< Destroy VM")
    vm.destroy(verify=True)

class TestXFSSRMultiHost:
    def test_cold_intrapool_migration(self, host, hostA2, vm_on_xfs_sr, xfs_sr, local_sr_on_hostA2):
        cold_migration_then_come_back(vm_on_xfs_sr, host, xfs_sr, hostA2, local_sr_on_hostA2)

    def test_live_intrapool_migration(self, host, hostA2, vm_on_xfs_sr, xfs_sr, local_sr_on_hostA2):
        live_storage_migration_then_come_back(vm_on_xfs_sr, host, xfs_sr, hostA2, local_sr_on_hostA2)

    def test_cold_crosspool_migration(self, host, hostB1, vm_on_xfs_sr, xfs_sr, local_sr_on_hostB1):
        cold_migration_then_come_back(vm_on_xfs_sr, host, xfs_sr, hostB1, local_sr_on_hostB1)

    def test_live_crosspool_migration(self, host, hostB1, vm_on_xfs_sr, xfs_sr, local_sr_on_hostB1):
        live_storage_migration_then_come_back(vm_on_xfs_sr, host, xfs_sr, hostB1, local_sr_on_hostB1)
