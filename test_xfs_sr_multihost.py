import pytest
from lib.common import wait_for, wait_for_not

# Requirements:
# From --hosts parameter:
# - hosts[0] first XCP-ng host >= 8.2 with an additional unused disk for the SR. Member of a pool that has at least two members. The second member can have any local SR.
# - hosts[1]: host of a separate pool, any local SR
# From --vm parameter
# - A VM to import to the EXT SR
# And:
# - access to XCP-ng RPM repository from hosts[0]

@pytest.fixture(scope='module')
def two_hosts_one_of_them_in_a_pool(hosts):
    # check requirements
    # two different pools
    assert hosts[0].pool.uuid != hosts[1].pool.uuid
    # the first one has at least 2 hosts
    assert len(hosts[0].pool.hosts) > 1
    yield

@pytest.fixture(scope='module')
def sr_disk(hosts):
    disks = hosts[0].disks()
    # there must be at least 2 disks
    assert len(disks) > 1, "at least two disks are required"
    # Using the second disk for SR
    yield disks[1]

@pytest.fixture(scope='module')
def xfs_sr(hosts, sr_disk):
    host = hosts[0]
    assert not host.file_exists('/usr/sbin/mkfs.xfs'), \
            "xfsprogs must not be installed on the host at the beginning of the tests"
    host.yum_install(['xfsprogs'])
    sr = host.sr_create('xfs', '/dev/' + sr_disk, "XFS-local-SR")
    yield sr
    # teardown
    sr.forget()
    host.yum_remove(['xfsprogs'])

@pytest.fixture(scope='module')
def vm_on_xfs_sr(xfs_sr, vm_ref):
    host = xfs_sr.host
    print(">> ", end='')
    vm = host.import_vm_url(vm_ref, sr_uuid=xfs_sr.uuid)
    yield vm
    # teardown
    print("<< Destroy VM")
    vm.destroy()
    wait_for_not(vm.exists, "<< Wait for VM destroyed")

#@pytest.mark.incremental
@pytest.mark.usefixtures("two_hosts_one_of_them_in_a_pool")
class TestXFSSRMultiHost:

    def test_cold_intrapool_migration(self, vm_on_xfs_sr):
        vm = vm_on_xfs_sr
        host = vm.host
        # Move the VM to another host of the pool
        host2 = host.pool.hosts[1]

    def test_cold_crosspool_migration(self, vm_on_xfs_sr):
        pass
