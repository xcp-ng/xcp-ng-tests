import pytest

@pytest.fixture(scope='session')
def host_with_zfs(host):
    assert not host.file_exists('/usr/sbin/zpool'), \
        "zfs must not be installed on the host at the beginning of the tests"
    host.yum_install(['zfs'], save_state=True)
    host.ssh(['modprobe', 'zfs'])
    yield host
    # teardown
    host.yum_restore_saved_state()

@pytest.fixture(scope='session')
def zpool_vol0(host_with_zfs, sr_disk_wiped):
    host_with_zfs.ssh(['zpool', 'create', '-f', 'vol0', '/dev/' + sr_disk_wiped])
    yield
    # teardown
    host_with_zfs.ssh(['zpool', 'destroy', 'vol0'])

@pytest.fixture(scope='session')
def zfs_sr(host, zpool_vol0):
    """ a ZFS SR on first host """
    sr = host.sr_create('zfs', "ZFS-local-SR", {'location': 'vol0'})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vm_on_zfs_sr(host, zfs_sr, vm_ref):
    print(">> ", end='')
    vm = host.import_vm(vm_ref, sr_uuid=zfs_sr.uuid)
    yield vm
    # teardown
    print("<< Destroy VM")
    vm.destroy(verify=True)
