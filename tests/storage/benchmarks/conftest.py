import pytest
import tempfile
import os
import logging

from lib.commands import SSHCommandFailed
from .helpers import load_results_from_csv

MAX_LENGTH = 64 * (1024**3) # 64GiB

# use vhd, qcow2, raw... when image_format support will be available
@pytest.fixture(scope="module", params=['vdi'])
def image_format(request):
    return request.param

@pytest.fixture(scope="module")
def running_unix_vm_with_fio(running_unix_vm):
    vm = running_unix_vm
    install_cmds = (
        ("command -v apt", "apt update && apt install -y fio", "apt remove -y fio"),
        ("command -v dnf", "dnf install -y fio", "dnf remove -y fio"),
        ("command -v yum", "yum install -y fio", "yum remove -y fio"),
        ("command -v apk", "apk add fio", "apk del fio")
    )

    for check_cmd, install_cmd, remove in install_cmds:
        try:
            vm.ssh(check_cmd, check=True)
            logging.info(f">> Installing fio with {install_cmd}")
            vm.ssh(install_cmd, check=True)
            remove_cmd = remove
            break
        except SSHCommandFailed:
            ...
    else:
        raise RuntimeError("Unsupported package manager: could not install fio")

    yield vm

    # teardown
    logging.info(f"<< Removing fio with {remove_cmd}")
    vm.ssh(remove_cmd, check=False)


@pytest.fixture(scope="module")
def vdi_on_local_sr(host, local_sr_on_hostA1, image_format):
    sr = local_sr_on_hostA1
    vdi = sr.create_vdi("testVDI", MAX_LENGTH)
    vdi.image_format = image_format
    logging.info(f">> Created VDI {vdi.uuid} of type {image_format}")

    yield vdi

    # teardown
    logging.info(f"<< Destroying VDI {vdi.uuid}")
    vdi.destroy()

@pytest.fixture(scope="module")
def plugged_vbd(vdi_on_local_sr, running_unix_vm_with_fio):
    vm = running_unix_vm_with_fio
    vdi = vdi_on_local_sr
    vbd = vm.create_vbd("autodetect", vdi.uuid)

    logging.info(f">> Plugging VDI {vdi.uuid} on VM {vm.uuid}")
    vbd.plug()

    yield vbd

    # teardown
    logging.info(f"<< Unplugging VDI {vdi.uuid} from VM {vm.uuid}")
    vbd.unplug()
    vbd.destroy()

@pytest.fixture(scope="module")
def local_temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir

@pytest.fixture(scope="module")
def temp_dir(running_unix_vm_with_fio):
    vm = running_unix_vm_with_fio
    tempdir = vm.ssh("mktemp -d")

    yield tempdir

    # teardown
    vm.ssh(f"rm -r {tempdir}")


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
