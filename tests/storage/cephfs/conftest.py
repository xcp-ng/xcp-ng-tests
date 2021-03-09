import pytest

def _setup_host_with_ceph(host):
    assert not host.file_exists('/usr/sbin/mount.ceph'), \
        "mount.ceph must not be installed on the host at the beginning of the tests"
    host.yum_install(['centos-release-ceph-jewel'], enablerepo="base,extras", save_state=True)
    host.yum_install(['ceph-common'], enablerepo="base,extras")

@pytest.fixture(scope='session')
def host_with_ceph(host):
    _setup_host_with_ceph(host)
    yield host
    # teardown
    host.yum_restore_saved_state()

@pytest.fixture(scope='session')
def hostA2_with_ceph(hostA2):
    _setup_host_with_ceph(hostA2)
    yield hostA2
    # teardown
    hostA2.yum_restore_saved_state()

@pytest.fixture(scope='session')
def cephfs_device_config(sr_device_config):
    if sr_device_config is not None:
        # SR device config from CLI param
        config = sr_device_config
    else:
        # SR device config from data.py defaults
        try:
            from data import DEFAULT_CEPHFS_DEVICE_CONFIG
        except ImportError:
            DEFAULT_CEPHFS_DEVICE_CONFIG = {}
        if DEFAULT_CEPHFS_DEVICE_CONFIG:
            config = DEFAULT_CEPHFS_DEVICE_CONFIG
        else:
            raise Exception("No default CephFS device-config found, neither in CLI nor in data.py defaults")
    return config

@pytest.fixture(scope='session')
def cephfs_sr(cephfs_device_config, host_with_ceph):
    """ a CephFS SR on first host """
    sr = host_with_ceph.sr_create('cephfs', "CephFS-SR", cephfs_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vm_on_cephfs_sr(host, cephfs_sr, vm_ref):
    print(">> ", end='')
    vm = host.import_vm(vm_ref, sr_uuid=cephfs_sr.uuid)
    yield vm
    # teardown
    print("<< Destroy VM")
    vm.destroy(verify=True)
