import logging
import pytest

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state, sr_disk_wiped

VOLUME_NAME = 'vol0'
VOLUME_PATH = '/' + VOLUME_NAME

@pytest.fixture(scope='package')
def host_without_zfs(host):
    assert not host.file_exists('/usr/sbin/zpool'), \
        "zfs must not be installed on the host at the beginning of the tests"

@pytest.fixture(scope='package')
def host_with_zfs(host_without_zfs, host_with_saved_yum_state):
    host = host_with_saved_yum_state
    host.yum_install(['zfs'])
    host.ssh(['modprobe', 'zfs'])
    yield host

@pytest.fixture(scope='package')
def zpool_vol0(sr_disk_wiped, host_with_zfs):
    host_with_zfs.ssh(['zpool', 'create', '-f', VOLUME_NAME, '/dev/' + sr_disk_wiped])
    yield
    # teardown
    host_with_zfs.ssh(['zpool', 'destroy', VOLUME_NAME])

@pytest.fixture(scope='package')
def zfs_sr(host, zpool_vol0):
    """ A ZFS SR on first host. """
    sr = host.sr_create('zfs', "ZFS-local-SR-test", {'location': VOLUME_PATH})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vm_on_zfs_sr(host, zfs_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=zfs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
