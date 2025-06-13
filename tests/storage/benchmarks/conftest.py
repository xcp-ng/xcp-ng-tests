import pytest
import tempfile
import os
import logging

from helpers import load_results_from_csv

@pytest.fixture(scope='package')
def ext_sr(host, sr_disk):
    sr = host.sr_create('ext', "EXT-local-SR-test", {'device': '/dev/' + sr_disk})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module', params=['raw', 'vhd', 'qcow2'])
def disk_on_ext_sr(request, ext_sr):
    disk_type = request.param
    disk = {}
    if disk_type == 'raw':
        ...
    elif disk_type == 'vhd':
        ...
    elif disk_type == 'qcow2':
        ...
    else:
        raise ValueError(f"Unsupported disk type: {disk_type}")

    yield disk

    # teardown
    ...

@pytest.fixture(scope='module')
def vm_on_ext_sr(host, ext_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=ext_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir

def pytest_addoption(parser):
    parser.addoption(
        "--prev-csv",
        action="store",
        default=None,
        help="Path to previous CSV results file for comparison",
    )

@pytest.fixture(scope="session")
def prev_results(request):
    csv_path = request.config.getoption("--prev-csv")
    results = {}
    if csv_path and os.path.exists(csv_path):
        load_results_from_csv(csv_path)
    return results