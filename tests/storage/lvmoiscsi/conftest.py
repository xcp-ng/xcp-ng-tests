import logging
import pytest

@pytest.fixture(scope='package')
def lvmoiscsi_device_config(sr_device_config):
    if sr_device_config is not None:
        # SR device config from CLI param
        config = sr_device_config
    else:
        # SR device config from data.py defaults
        try:
            from data import DEFAULT_LVMOISCSI_DEVICE_CONFIG
        except ImportError:
            DEFAULT_LVMOISCSI_DEVICE_CONFIG = {}
        if DEFAULT_LVMOISCSI_DEVICE_CONFIG:
            config = DEFAULT_LVMOISCSI_DEVICE_CONFIG
        else:
            raise Exception("No default lvmoiscsi device-config found, neither in CLI nor in data.py defaults")
    return config

@pytest.fixture(scope='package')
def lvmoiscsi_sr(host, lvmoiscsi_device_config):
    """ A lvmoiscsi SR on first host. """
    sr = host.sr_create('lvmoiscsi', "lvmoiscsi-SR-test", lvmoiscsi_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vm_on_lvmoiscsi_sr(host, lvmoiscsi_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=lvmoiscsi_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
