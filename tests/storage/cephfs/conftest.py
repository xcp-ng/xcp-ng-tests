import logging

import pytest

from lib import config
from lib.common import exec_nofail, raise_errors

# explicit import for package-scope fixtures
from pkgfixtures import pool_with_saved_yum_state

@pytest.fixture(scope='package')
def pool_without_ceph(host):
    for h in host.pool.hosts:
        assert not host.file_exists('/usr/sbin/mount.ceph'), \
            "mount.ceph must not be installed on the host at the beginning of the tests"
    yield host.pool

@pytest.fixture(scope='package')
def pool_with_ceph(pool_without_ceph, pool_with_saved_yum_state):
    pool = pool_with_saved_yum_state
    for h in pool.hosts:
        h.yum_install(['centos-release-ceph-jewel'], enablerepo="base,extras")
        h.yum_install(['ceph-common'], enablerepo="base,extras")
    yield pool

@pytest.fixture(scope='package')
def cephfs_device_config():
    return config.sr_device_config("CEPHFS_DEVICE_CONFIG")

@pytest.fixture(scope='package')
def cephfs_sr(host, cephfs_device_config, pool_with_ceph):
    """ A CephFS SR on first host. """
    sr = host.sr_create('cephfs', "CephFS-SR-test", cephfs_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_cephfs_sr(cephfs_sr):
    vdi = cephfs_sr.create_vdi('CephFS-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_cephfs_sr(host, cephfs_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=cephfs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
