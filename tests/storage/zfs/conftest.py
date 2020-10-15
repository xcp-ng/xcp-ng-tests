import pytest

CMD_ZPOOL = 'zpool'
VOLUME_NAME = 'vol0'
VOLUME_PATH = '/' + VOLUME_NAME
ZFS = 'zfs'

@pytest.fixture(scope='session')
def host_with_zfs(host):
    assert not host.file_exists('/usr/sbin/zpool'), \
        "zfs must not be installed on the host at the beginning of the tests"
    host.yum_install([ZFS], save_state=True)
    host.ssh(['modprobe', ZFS])
    yield host
    # teardown
    host.yum_restore_saved_state()

@pytest.fixture(scope='session')
def zpool_vol0(host_with_zfs, sr_disk_wiped):
    host_with_zfs.ssh([CMD_ZPOOL, 'create', '-f', VOLUME_NAME, '/dev/' + sr_disk_wiped])
    yield
    # teardown
    host_with_zfs.ssh([CMD_ZPOOL, 'destroy', VOLUME_NAME])

@pytest.fixture(scope='session')
def zfs_sr(host, zpool_vol0):
    """ a ZFS SR on first host """
    sr = host.sr_create(ZFS, "ZFS-local-SR", {'location': VOLUME_PATH})
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
