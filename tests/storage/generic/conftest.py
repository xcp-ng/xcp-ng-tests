import logging
import pytest

SR_TYPES = ["ext", "lvm"]

def pytest_generate_tests(metafunc):
    if "sr_type" in metafunc.fixturenames:
        cmdline_sr_type = metafunc.config.getoption("sr_type")
        if "all" in cmdline_sr_type:
            metafunc.parametrize("sr_type", SR_TYPES, scope="package")
        else:
            metafunc.parametrize("sr_type", cmdline_sr_type, scope="package")

@pytest.fixture(scope='package')
def generic_sr(host, sr_disk, sr_type):
    """ A sr_type SR on first host. """
    sr_name = "{}-local-SR-test".format(sr_type.upper())
    sr = host.sr_create(sr_type, sr_name, {'device': '/dev/' + sr_disk})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_generic_sr(generic_sr):
    vdi = generic_sr.create_vdi('GEN-local-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_generic_sr(host, generic_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=generic_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
