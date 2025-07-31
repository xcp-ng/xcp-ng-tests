import itertools
import logging
import os
import tempfile
import urllib.request
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from lib.commands import SSHCommandFailed
from lib.common import PackageManagerEnum

from .helpers import load_results_from_csv, str_to_tuple

MAX_LENGTH = 64 * (1024**3)  # 64GiB


# use vhd, qcow2, raw... when image_format support will be available
@pytest.fixture(scope="module", params=["vhd"])
def image_format(request):
    return request.param


@pytest.fixture(scope="module")
def running_unix_vm_with_fio(running_unix_vm):
    vm = running_unix_vm
    snapshot = vm.snapshot()

    package_cmds = {
        PackageManagerEnum.APT_GET.value: "apt-get update && apt install -y fio",
        PackageManagerEnum.RPM.value: "yum install -y fio",
        PackageManagerEnum.UNKNOWN.value: "apk add fio",
    }

    package_manager = vm.detect_package_manager().value
    try:
        vm.ssh(package_cmds[package_manager])
        logging.info(f">> Installing fio with {package_cmds[package_manager]}")
    except SSHCommandFailed:
        raise RuntimeError("Unsupported package manager: could not install fio")

    yield vm

    # teardown
    try:
        snapshot.revert()
    finally:
        snapshot.destroy()


@pytest.fixture(scope="module")
def vdi_on_local_sr(host, local_sr_on_hostA1, image_format):
    sr = local_sr_on_hostA1
    vdi = sr.create_vdi("testVDI", MAX_LENGTH)  # , image_format=image_format)
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
    system_memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")

    parser.addoption(
        "--prev-csv",
        action="store",
        default=None,
        help="Path/URI to previous CSV results file for comparison",
    )
    parser.addoption(
        "--block-sizes",
        action="store",
        type=lambda value: str_to_tuple(value, sep=","),
        default=("4k", "16k", "64k", "1M"),
        help="Comma separated values of block sizes to test in disk benchmarks",
    )
    parser.addoption(
        "--file-sizes",
        action="store",
        type=lambda value: str_to_tuple(value, sep=","),
        default=("1G", "4G", f"{int((system_memory // (1024.**3)) * 2)}G"), # (2*Memory) GiB
        help="Comma separated values of file sizes to test in disk benchmarks",
    )
    parser.addoption(
        "--modes",
        action="store",
        type=lambda value: str_to_tuple(value, sep=","),
        default=("read", "randread", "write", "randwrite"),
        help="Comma separated values of rw_modes to test in disk benchmarks",
    )
    parser.addoption(
        "--numjobs",
        action="store",
        default=1,
        help="Mapped to fio's --numjobs",
    )
    parser.addoption(
        "--iodepth",
        action="store",
        default=1,
        help="Mapped to fio's --iodepth",
    )
    parser.addoption(
        "--regression_threshold",
        action="store",
        default=10,
        help="Percentage of regression that will cause the test to fail",
    )
    parser.addoption(
        "--improvement_threshold",
        action="store",
        default=10,
        help="Minimum percentage of improvement considered significant enough to report",
    )


def pytest_generate_tests(metafunc):
    if {"block_size", "file_size", "rw_mode"} <= set(metafunc.fixturenames):
        block_sizes = metafunc.config.getoption("block_sizes")
        file_sizes = metafunc.config.getoption("file_sizes")
        modes = metafunc.config.getoption("modes")

        test_cases = list(itertools.product(block_sizes, file_sizes, modes))
        metafunc.parametrize("block_size,file_size,rw_mode", test_cases)


@pytest.fixture(scope="session")
def prev_results(pytestconfig):
    csv_uri = pytestconfig.getoption("--prev-csv")
    if not csv_uri:
        return {}
    csv_path = csv_uri
    if urlparse(csv_uri).scheme != "":
        logging.info("Detected CSV path as an url")
        csv_path = f"/tmp/{uuid4()}.csv"
        urllib.request.urlretrieve(csv_uri, csv_path)
        logging.info(f"Fetching CSV file from {csv_uri} to {csv_path}")
    if not os.path.exists(csv_path):
        raise FileNotFoundError(csv_path)
    return load_results_from_csv(csv_path)
