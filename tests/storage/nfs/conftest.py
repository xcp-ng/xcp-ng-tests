import logging
import pytest

@pytest.fixture(scope='session')
def nfs_device_config(sr_device_config):
    if sr_device_config is not None:
        # SR device config from CLI param
        config = sr_device_config
    else:
        # SR device config from data.py defaults
        try:
            from data import DEFAULT_NFS_DEVICE_CONFIG
        except ImportError:
            DEFAULT_NFS_DEVICE_CONFIG = {}
        if DEFAULT_NFS_DEVICE_CONFIG:
            config = DEFAULT_NFS_DEVICE_CONFIG
        else:
            raise Exception("No default NFS device-config found, neither in CLI nor in data.py defaults")
    return config

@pytest.fixture(scope='session')
def nfs_sr(host, nfs_device_config):
    """ A NFS SR on first host. """
    sr = host.sr_create('nfs', "NFS-SR-test", nfs_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vm_on_nfs_sr(host, nfs_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=nfs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
