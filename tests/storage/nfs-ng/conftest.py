import logging
import pytest

# --- NFS3 fixtures ------------------------------------------------------------

@pytest.fixture(scope='package')
def nfsng_device_config(sr_device_config):
    if sr_device_config is not None:
        # SR device config from CLI param
        config = sr_device_config
    else:
        # SR device config from data.py defaults
        try:
            from data import DEFAULT_NFSNG_DEVICE_CONFIG
        except ImportError:
            DEFAULT_NFSNG_DEVICE_CONFIG = {}
        if DEFAULT_NFSNG_DEVICE_CONFIG:
            config = DEFAULT_NFSNG_DEVICE_CONFIG
        else:
            raise Exception("No default NFS-ng device-config found, neither in CLI nor in data.py defaults")
    return config

@pytest.fixture(scope='package')
def nfsng_sr(host, nfsng_device_config):
    """ A NFS SR on first host. """
    sr = host.sr_create('nfs', "NFS-SR-test", nfsng_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_nfsng_sr(nfsng_sr):
    vdi = nfsng_sr.create_vdi('NFS-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_nfsng_sr(host, nfsng_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=nfsng_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
