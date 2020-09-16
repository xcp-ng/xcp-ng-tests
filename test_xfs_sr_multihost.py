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
    """ a free disk for the XFS SR on first host """
    disks = hosts[0].disks()
    # there must be at least 2 disks
    assert len(disks) > 1, "at least two disks are required"
    # Using the second disk for SR
    yield disks[1]

@pytest.fixture(scope='module')
def xfs_sr(hosts, sr_disk):
    """ a XFS SR on first host """
    host = hosts[0]
    assert not host.file_exists('/usr/sbin/mkfs.xfs'), \
            "xfsprogs must not be installed on the host at the beginning of the tests"
    host.yum_install(['xfsprogs'])
    sr = host.sr_create('xfs', '/dev/' + sr_disk, "XFS-local-SR")
    yield sr
    # teardown
    sr.destroy()
    host.yum_remove(['xfsprogs'])

@pytest.fixture(scope='module')
def local_sr_on_pool_other_host(hosts):
    """ a local SR on the first pool's second host """
    host = hosts[0].pool.hosts[1]
    srs = host.local_vm_srs()
    assert len(srs) > 0, "a local SR is required on the pool's second host"
    # use the first local SR found
    yield srs[0]

@pytest.fixture(scope='module')
def local_sr_on_other_pool(hosts):
    """ a local SR on the second pool's master """
    host = hosts[1]
    srs = host.local_vm_srs()
    assert len(srs) > 0, "a local SR is required on the second pool's master"
    # use the first local SR found
    yield srs[0]

@pytest.fixture(scope='module')
def vm_on_xfs_sr(xfs_sr, vm_ref, hosts):
    host = hosts[0]
    print(">> ", end='')
    vm = host.import_vm_url(vm_ref, sr_uuid=xfs_sr.uuid)
    yield vm
    # teardown
    print("<< Destroy VM")
    vm.destroy(verify=True)

#@pytest.mark.incremental
@pytest.mark.usefixtures("two_hosts_one_of_them_in_a_pool")
class TestXFSSRMultiHost:

    def test_cold_intrapool_migration(self, vm_on_xfs_sr, xfs_sr, local_sr_on_pool_other_host):
        vm = vm_on_xfs_sr
        assert vm.is_halted()
        host = vm.host
        # Move the VM to another host of the pool
        host2 = host.pool.hosts[1]
        vm.migrate(host2, local_sr_on_pool_other_host)
        wait_for(lambda: vm.all_vdis_on_host(host2), "Wait for all VDIs on host2")
        # Start VM to make sure it works
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)
        # Migrate it back to the first host on XFS SR
        vm.migrate(host, xfs_sr)
        wait_for(lambda: vm.all_vdis_on_host(host), "Wait for all VDIs back on host")
        # Start VM to make sure it works
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    def test_live_intrapool_migration(self, vm_on_xfs_sr, xfs_sr, local_sr_on_pool_other_host):
        vm = vm_on_xfs_sr
        host = vm.host
        # start VM
        vm.start()
        vm.wait_for_os_booted()
        # Move the VM to another host of the pool
        host2 = host.pool.hosts[1]
        vm.migrate(host2, local_sr_on_pool_other_host)
        wait_for(lambda: vm.all_vdis_on_host(host2), "Wait for all VDIs on host2")
        wait_for(lambda: vm.is_running_on_host(host2), "Wait for VM to be running on host2")
        # Migrate it back to the first host on XFS SR
        vm.migrate(host, xfs_sr)
        wait_for(lambda: vm.all_vdis_on_host(host), "Wait for all VDIs back on host")
        wait_for(lambda: vm.is_running_on_host(host), "Wait for VM to be running on host")
        vm.shutdown(verify=True)

    def test_cold_crosspool_migration(self, hosts, vm_on_xfs_sr, xfs_sr, local_sr_on_other_pool):
        vm = vm_on_xfs_sr
        assert vm.is_halted()
        host = vm.host
        # Move the VM to the other pool
        host2 = hosts[1]
        vm.migrate(host2, local_sr_on_other_pool)
        wait_for(lambda: vm.all_vdis_on_host(host2), "Wait for all VDIs on host2")
        # Start VM to make sure it works
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)
        # Migrate it back to the first host on XFS SR
        vm.migrate(host, xfs_sr)
        wait_for(lambda: vm.all_vdis_on_host(host), "Wait for all VDIs back on host")
        # Start VM to make sure it works
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    def test_live_crosspool_migration(self, hosts, vm_on_xfs_sr, xfs_sr, local_sr_on_other_pool):
        vm = vm_on_xfs_sr
        host = vm.host
        # start VM
        vm.start()
        vm.wait_for_os_booted()
        # Move the VM to the other pool
        host2 = hosts[1]
        vm.migrate(host2, local_sr_on_other_pool)
        wait_for(lambda: vm.all_vdis_on_host(host2), "Wait for all VDIs on host2")
        wait_for(lambda: vm.is_running_on_host(host2), "Wait for VM to be running on host2")
        # Migrate it back to the first host on XFS SR
        vm.migrate(host, xfs_sr)
        wait_for(lambda: vm.all_vdis_on_host(host), "Wait for all VDIs back on host")
        wait_for(lambda: vm.is_running_on_host(host), "Wait for VM to be running on host")
        vm.shutdown(verify=True)
